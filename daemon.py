#!/usr/bin/env python3
"""
Knob Controller Daemon (Python + StreamDock SDK)

Uses the proprietary libtransport C library for HID access (bypasses macOS Tahoe
IOKit restrictions that block node-hid and standard hidapi).

Knobs:
  Big knob (KNOB_3)       -> Audio Pro C10 MkII volume (press to mute)
  Bottom-right (KNOB_2)   -> Mac system volume (press to mute)
  Bottom-left (KNOB_1)    -> A15 volume (press to mute)

Buttons (KEY_1-KEY_6):
  Configurable via buttons.json — open apps, folders, URLs
"""
import json
import logging
import sys
import os
import signal
import subprocess
import threading
import time
import concurrent.futures
import urllib.request

# Quiet the zeroconf/pychromecast background threads: when a Zeroconf instance
# dies mid-use their reconnect threads traceback in a tight loop (this once
# wrote a 155GB stderr log). Errors are handled at our call sites instead.
logging.getLogger("zeroconf").setLevel(logging.CRITICAL)
logging.getLogger("pychromecast").setLevel(logging.CRITICAL)

_thread_exc_last = {}

def _rate_limited_thread_excepthook(args):
    """Print each distinct thread exception at most once per minute."""
    key = (getattr(args.exc_type, "__name__", "?"), str(args.exc_value)[:200])
    now = time.time()
    if now - _thread_exc_last.get(key, 0) > 60:
        _thread_exc_last[key] = now
        print(f"Thread exception ({key[0]}): {args.exc_value}", flush=True)

threading.excepthook = _rate_limited_thread_excepthook

# Hard backstop: launchd appends stdout/stderr to these with no rotation,
# so truncate them if anything ever floods again.
_LOG_PATHS = ("/Library/Logs/knob-controller/err.log", "/Library/Logs/knob-controller/out.log")
_LOG_CAP_BYTES = 50 * 1024 * 1024

def _log_watchdog():
    while True:
        for path in _LOG_PATHS:
            try:
                if os.path.getsize(path) > _LOG_CAP_BYTES:
                    os.truncate(path, 0)
                    print(f"[watchdog] truncated {path} (exceeded 50MB)", flush=True)
            except OSError:
                pass
        time.sleep(30)

# Hang watchdog: launchd KeepAlive only restarts a process that exits. On
# 2026-09-23 the daemon spun at ~190% CPU for 17 days while still "alive".
# Exiting on purpose hands recovery back to launchd. Idle CPU is ~0%.
_SPIN_CPU_FRACTION = 0.8   # of one core, averaged over a sample window
_SPIN_SAMPLE_SECS = 60
_SPIN_STRIKES = 3          # consecutive hot windows before exiting

def _hang_watchdog(deck):
    strikes = 0
    last = sum(os.times()[:2])
    while True:
        time.sleep(_SPIN_SAMPLE_SECS)
        now = sum(os.times()[:2])
        busy = (now - last) / _SPIN_SAMPLE_SECS
        last = now
        strikes = strikes + 1 if busy > _SPIN_CPU_FRACTION else 0
        if strikes >= _SPIN_STRIKES:
            print(f"[watchdog] CPU {busy:.0%} for {strikes} min — exiting for launchd restart", flush=True)
            os._exit(3)
        if not (deck.read_thread and deck.read_thread.is_alive()):
            print("[watchdog] HID reader thread died (device unplugged?) — exiting for launchd restart", flush=True)
            os._exit(4)

SDK_PATH = os.path.join(os.path.dirname(__file__), "StreamDock-Device-SDK/Python-SDK/src")
sys.path.insert(0, SDK_PATH)

from StreamDock.DeviceManager import DeviceManager
from StreamDock.InputTypes import EventType, KnobId, Direction, ButtonKey

# ─── Config ───
# Bottom-right knob target. Audio Pro C10 MkII has Chromecast built-in,
# so it's controlled as a cast device (same path as the Nest Hub / A15).
NEST_HUB_NAME = "C10"
# Cast UUIDs survive renames (the C10 silently went from
# "AudioPro_C10MkII_2648" to "C10" in Oct 2026); names are only a fallback.
C10_UUID = "fc265572-edc0-ea14-cc3b-f889c2e40494"
A15_UUID = "46fd8aea-75b1-ec0a-c390-22cea46b610c"
SYSTEM_VOL_STEP = 5
CAST_VOL_STEP = 5  # percentage
CSUB_IP = "10.0.0.5"
BUTTONS_CONFIG = os.path.join(os.path.dirname(__file__), "buttons.json")
ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


# ─── Button Launcher ───
_button_map = {}  # ButtonKey -> {action, target, label}

def load_buttons():
    """Load button config from buttons.json."""
    global _button_map
    try:
        with open(BUTTONS_CONFIG) as f:
            buttons = json.load(f)
        for btn in buttons:
            key = ButtonKey(btn["key"])
            _button_map[key] = {
                "action": btn["action"],
                "target": btn["target"],
                "label": btn["label"],
            }
        print(f"Loaded {len(_button_map)} button configs", flush=True)
    except Exception as e:
        print(f"Button config error: {e}", flush=True)

def push_button_icons(deck):
    """Push icon images to the 6 LCD buttons."""
    for key_num in range(1, 7):
        icon_path = os.path.join(ICONS_DIR, f"key_{key_num}.jpg")
        if os.path.exists(icon_path):
            try:
                deck.set_key_image(key_num, icon_path)
                print(f"  Key {key_num}: icon set", flush=True)
            except Exception as e:
                print(f"  Key {key_num} icon error: {e}", flush=True)

def handle_button(key):
    """Handle a button press — open app, folder, URL, or toggle A15 playback."""
    btn = _button_map.get(key)
    if not btn:
        return
    action = btn["action"]
    target = btn["target"]
    label = btn["label"]
    print(f"Button: {label} ({action}: {target})", flush=True)

    try:
        if action == "app":
            subprocess.Popen(
                AS_USER + [f'open -a "{target}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif action == "folder":
            subprocess.Popen(
                AS_USER + [f'open "{target}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif action == "url":
            subprocess.Popen(
                AS_USER + [f'open -a "Google Chrome" "{target}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif action == "airplay":
            _executor.submit(_toggle_a15_playback)
        elif action == "csub_toggle":
            csub_toggle()
    except Exception as e:
        print(f"Button error: {e}", flush=True)

# ─── C-Sub Volume (LinkPlay HTTP API) ───
_csub_on = False
_csub_prev_vol = 50

def _csub_cmd(cmd):
    """Send a command to C-Sub via LinkPlay HTTP API."""
    url = f"http://{CSUB_IP}/httpapi.asp?command={cmd}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"C-Sub HTTP error: {e}", flush=True)
        return None

def _csub_get_volume():
    """Get current C-Sub volume (0-100)."""
    raw = _csub_cmd("getPlayerStatus")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return int(data.get("vol", 50))
    except (json.JSONDecodeError, ValueError):
        return None

def _csub_set_volume(vol):
    vol = max(0, min(100, vol))
    _csub_cmd(f"setPlayerCmd:vol:{vol}")
    print(f"C-Sub: {vol}%", flush=True)

def _toggle_csub():
    """Toggle C-Sub on/off (mute to 0 / restore previous volume)."""
    global _csub_on, _csub_prev_vol
    if _csub_on:
        current = _csub_get_volume()
        if current is not None and current > 0:
            _csub_prev_vol = current
        _csub_set_volume(0)
        _csub_on = False
        print("C-Sub: OFF", flush=True)
    else:
        restore = _csub_prev_vol if _csub_prev_vol > 0 else 50
        _csub_set_volume(restore)
        _csub_on = True
        print(f"C-Sub: ON ({restore}%)", flush=True)

def csub_toggle():
    _executor.submit(_toggle_csub)


_a15_active = False

def _toggle_a15_playback():
    """Toggle A15 volume: 40% on, mute off.

    Use with a Google Home speaker group ('Desk') for synced playback.
    Say 'Hey Google, play on Desk' to start both Hub + A15, then use
    this button to mute/unmute the A15 within the group.
    """
    global _a15_active
    a15 = _get_a15()
    if not a15:
        print("A15: offline", flush=True)
        return

    try:
        if _a15_active:
            a15.set_volume(0)
            _a15_active = False
            print("A15: MUTED", flush=True)
        else:
            a15.set_volume(0.4)
            _a15_active = True
            print("A15: ON at 40%", flush=True)
    except Exception as e:
        _a15_active = False
        print(f"A15 button error: {e}", flush=True)
        if _is_dead_zeroconf(e):
            _reset_cast_caches()


# ─── Mac System Volume ───
# Daemon runs as root, but osascript needs the user's audio session.
# Run as the logged-in user to control headphones/speakers/AirPods.
AS_USER = ["su", "-", "dymirtatem", "-c"]

def get_mac_volume():
    try:
        out = subprocess.check_output(
            AS_USER + ['osascript -e "output volume of (get volume settings)"'], text=True
        )
        return int(out.strip())
    except Exception:
        return 50

def set_mac_volume(vol):
    vol = max(0, min(100, vol))
    subprocess.run(AS_USER + [f'osascript -e "set volume output volume {vol}"'], check=False)
    print(f"Mac: {vol}%", flush=True)
    return vol

def toggle_mac_mute():
    try:
        out = subprocess.check_output(
            AS_USER + ['osascript -e "output muted of (get volume settings)"'], text=True
        )
        muted = out.strip() == "true"
        subprocess.run(AS_USER + [f'osascript -e "set volume output muted {not muted}"'], check=False)
        print(f"Mac: {'muted' if not muted else 'unmuted'}", flush=True)
    except Exception as e:
        print(f"Mac mute error: {e}", flush=True)


# ─── Nest Hub Volume (sync, runs in thread pool) ───
_hub_cast = None
_hub_browser = None
_hub_lock = threading.Lock()

def _get_hub():
    global _hub_cast, _hub_browser
    if _hub_cast is not None:
        return _hub_cast
    try:
        import pychromecast
        casts, browser = pychromecast.get_chromecasts(timeout=10)
        for c in casts:
            c.wait(timeout=5)
        hub = _find_cast(casts, C10_UUID, NEST_HUB_NAME)
        if hub:
            _hub_cast = hub
            _hub_browser = browser
            print(f"Connected to {NEST_HUB_NAME}", flush=True)
            return _hub_cast
        browser.stop_discovery()
    except Exception as e:
        print(f"Hub connect error: {e}", flush=True)
    return None

def _hub_adjust_volume(delta):
    with _hub_lock:
        hub = _get_hub()
        if not hub:
            print("Hub: offline", flush=True)
            return
        try:
            current = hub.status.volume_level
            new_vol = max(0.0, min(1.0, current + delta / 100.0))
            hub.set_volume(new_vol)
            print(f"Hub: {int(new_vol * 100)}%", flush=True)
        except Exception as e:
            print(f"Hub error: {e}", flush=True)
            if _is_dead_zeroconf(e):
                _reset_cast_caches()

def _hub_toggle_mute():
    with _hub_lock:
        hub = _get_hub()
        if not hub:
            return
        try:
            new_muted = not hub.status.volume_muted
            hub.set_volume_muted(new_muted)
            print(f"Hub: {'muted' if new_muted else 'unmuted'}", flush=True)
        except Exception as e:
            print(f"Hub mute error: {e}", flush=True)
            if _is_dead_zeroconf(e):
                _reset_cast_caches()

def hub_adjust(delta):
    _executor.submit(_hub_adjust_volume, delta)

def hub_mute():
    _executor.submit(_hub_toggle_mute)


# ─── A15 Volume (Cast device — syncs with Hub) ───
_a15_cast = None
_a15_browser = None
_a15_lock = threading.Lock()
_a15_muted = False
_a15_prev_vol = 0.5

# ─── Double-Click Detector ───
class DoubleClickDetector:
    """Detect single vs double press on a knob.

    Double-click fires immediately on the second press.
    Single-click fires after the timeout with no second press.
    """
    TIMEOUT = 0.4  # seconds

    def __init__(self, on_single, on_double):
        self.on_single = on_single
        self.on_double = on_double
        self._timer = None
        self._last_press = 0

    def press(self):
        now = time.time()
        elapsed = now - self._last_press

        if elapsed < self.TIMEOUT:
            # Double-click — cancel pending single-click and fire double
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._last_press = 0
            self.on_double()
        else:
            # First press — start timer for single-click
            self._last_press = now
            self._timer = threading.Timer(self.TIMEOUT, self._fire_single)
            self._timer.daemon = True
            self._timer.start()

    def _fire_single(self):
        self._last_press = 0
        self._timer = None
        self.on_single()

def _get_a15():
    global _a15_cast, _a15_browser
    if _a15_cast is not None:
        return _a15_cast
    try:
        import pychromecast
        casts, browser = pychromecast.get_chromecasts(timeout=10)
        for c in casts:
            c.wait(timeout=5)
        a15 = _find_cast(casts, A15_UUID, "A15")
        if a15:
            _a15_cast = a15
            # Keep the browser referenced: its Zeroconf instance backs the
            # cast's reconnect logic. Losing it to GC caused a crash loop.
            _a15_browser = browser
            print(f"Connected to A15", flush=True)
            return _a15_cast
        browser.stop_discovery()
    except Exception as e:
        print(f"A15 connect error: {e}", flush=True)
    return None


def _reset_cast_caches():
    """Drop cached cast connections so the next use rediscovers from scratch.

    Called when a cast operation hits a dead Zeroconf instance
    ("Zeroconf instance loop must be running") — the cached objects are
    unusable and their reconnect threads would loop forever.
    """
    global _hub_cast, _hub_browser, _a15_cast, _a15_browser
    for browser in (_hub_browser, _a15_browser):
        try:
            if browser is not None:
                browser.stop_discovery()
        except Exception:
            pass
    _hub_cast = None
    _hub_browser = None
    _a15_cast = None
    _a15_browser = None
    print("Cast caches reset — will rediscover on next use", flush=True)


def _find_cast(casts, uuid, name):
    """Match a discovered cast by UUID, then by name; log what was seen on a miss."""
    for c in casts:
        if str(c.uuid) == uuid:
            return c
    for c in casts:
        if c.name == name:
            return c
    seen = ", ".join(f"{c.name} ({c.uuid})" for c in casts) or "none"
    print(f"Cast '{name}' not found. Visible: {seen}", flush=True)
    return None


def _is_dead_zeroconf(exc):
    return isinstance(exc, AssertionError) or "Zeroconf" in str(exc)

def _a15_adjust_volume(delta):
    global _a15_muted
    with _a15_lock:
        a15 = _get_a15()
        if not a15:
            print("A15: offline", flush=True)
            return
        try:
            current = a15.status.volume_level
            new_vol = max(0.0, min(1.0, current + delta / 100.0))
            a15.set_volume(new_vol)
            _a15_muted = False
            print(f"A15: {int(new_vol * 100)}%", flush=True)
        except Exception as e:
            print(f"A15 error: {e}", flush=True)
            if _is_dead_zeroconf(e):
                _reset_cast_caches()

def _a15_toggle():
    """Press to toggle A15 on/off — mutes to 0 or restores previous volume."""
    global _a15_muted, _a15_prev_vol
    with _a15_lock:
        a15 = _get_a15()
        if not a15:
            print("A15: offline", flush=True)
            return
        try:
            if not _a15_muted:
                _a15_prev_vol = a15.status.volume_level
                a15.set_volume(0)
                _a15_muted = True
                print("A15: OFF", flush=True)
            else:
                restore = _a15_prev_vol if _a15_prev_vol > 0 else 0.5
                a15.set_volume(restore)
                _a15_muted = False
                print(f"A15: ON ({int(restore * 100)}%)", flush=True)
        except Exception as e:
            print(f"A15 toggle error: {e}", flush=True)
            if _is_dead_zeroconf(e):
                _reset_cast_caches()

def a15_adjust(delta):
    _executor.submit(_a15_adjust_volume, delta)

def a15_toggle():
    _executor.submit(_a15_toggle)


def _a15_sync_to_hub():
    """Double-click: get what's playing on Hub, extract audio URL, cast to A15."""
    with _hub_lock:
        hub = _get_hub()
    if not hub:
        print("A15 sync: Hub offline", flush=True)
        return

    mc = hub.media_controller
    if not mc.status or not mc.status.content_id:
        print("A15 sync: nothing playing on Hub", flush=True)
        return

    content_id = mc.status.content_id
    content_type = mc.status.content_type or ""
    player_state = mc.status.player_state
    print(f"A15 sync: Hub playing {content_id} ({content_type}, {player_state})", flush=True)

    # For YouTube, use yt-dlp to get a direct audio stream URL
    if "youtube" in content_type:
        try:
            url = f"https://www.youtube.com/watch?v={content_id}"
            result = subprocess.run(
                ["/opt/homebrew/bin/yt-dlp", "-f", "bestaudio", "--get-url", url],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0 or not result.stdout.strip():
                print(f"A15 sync: yt-dlp failed: {result.stderr[:200]}", flush=True)
                return
            audio_url = result.stdout.strip()
            content_type = "audio/webm"
            print("A15 sync: got audio URL from yt-dlp", flush=True)
        except Exception as e:
            print(f"A15 sync: yt-dlp error: {e}", flush=True)
            return
    else:
        audio_url = content_id

    with _a15_lock:
        a15 = _get_a15()
        if not a15:
            print("A15 sync: A15 offline", flush=True)
            return
        try:
            a15.wait()
            a15.set_volume(0.5)
            a15.media_controller.play_media(audio_url, content_type)
            a15.media_controller.block_until_active(timeout=10)
            global _a15_muted, _a15_prev_vol
            _a15_muted = False
            _a15_prev_vol = 0.5
            print("A15 sync: NOW PLAYING at 50%!", flush=True)
        except Exception as e:
            print(f"A15 sync error: {e}", flush=True)
            if _is_dead_zeroconf(e):
                _reset_cast_caches()

def a15_sync():
    _executor.submit(_a15_sync_to_hub)


# ─── Event Handler ───

# Double-click detector for A15 knob: single=mute, double=sync to Hub
_a15_click = DoubleClickDetector(on_single=a15_toggle, on_double=a15_sync)

def on_event(event):
    # Button press → launch app/folder/URL
    if event.event_type == EventType.BUTTON and event.state == 1:
        handle_button(event.key)
        return

    if event.event_type == EventType.KNOB_ROTATE:
        if event.knob_id == KnobId.KNOB_3:  # Big knob -> C10 MkII
            delta = CAST_VOL_STEP if event.direction == Direction.RIGHT else -CAST_VOL_STEP
            hub_adjust(delta)
        elif event.knob_id == KnobId.KNOB_2:  # Bottom-right -> Mac volume
            delta = SYSTEM_VOL_STEP if event.direction == Direction.RIGHT else -SYSTEM_VOL_STEP
            current = get_mac_volume()
            set_mac_volume(current + delta)
        elif event.knob_id == KnobId.KNOB_1:  # Bottom-left -> A15
            delta = SYSTEM_VOL_STEP if event.direction == Direction.RIGHT else -SYSTEM_VOL_STEP
            a15_adjust(delta)

    elif event.event_type == EventType.KNOB_PRESS and event.state == 1:
        if event.knob_id == KnobId.KNOB_3:
            hub_mute()
        elif event.knob_id == KnobId.KNOB_2:
            toggle_mac_mute()
        elif event.knob_id == KnobId.KNOB_1:
            _a15_click.press()  # Single=mute, Double=sync to Hub


# ─── Main ───
def main():
    print("Knob Controller starting...", flush=True)

    threading.Thread(target=_log_watchdog, daemon=True).start()

    manager = DeviceManager()
    decks = manager.enumerate()

    if not decks:
        print("No StreamDock devices found, retrying in 5s...", flush=True)
        time.sleep(5)
        decks = manager.enumerate()
        if not decks:
            print("Still no device. Exiting.", flush=True)
            sys.exit(1)

    deck = decks[0]
    print(f"Found: VID={hex(deck.vendor_id)} PID={hex(deck.product_id)}", flush=True)

    deck.open()
    deck.init()
    print(f"Firmware: {deck.firmware_version}", flush=True)

    # Load button config and push icons to LCD buttons
    load_buttons()
    print("Pushing button icons...", flush=True)
    push_button_icons(deck)

    # Pre-connect to Hub and A15 in background
    _executor.submit(_get_hub)
    _executor.submit(_get_a15)

    print("\nKnob Controller running!", flush=True)
    print("  Big knob       -> C10 MkII volume (press to mute)", flush=True)
    print("  Bottom-right   -> Mac volume (press to mute)", flush=True)
    print("  Bottom-left    -> A15 volume (press to mute)", flush=True)
    for key, btn in _button_map.items():
        print(f"  Key {key.value}          -> {btn['label']} ({btn['action']})", flush=True)

    # Use SDK callback instead of manual read loop
    # (SDK's open() starts an internal reader thread — manual read() competes with it)
    # Callback signature: callback(device, event)
    deck.set_key_callback(lambda device, event: on_event(event))

    # Debug: verify reader thread is alive
    if deck.read_thread and deck.read_thread.is_alive():
        print("Reader thread: ALIVE", flush=True)
    else:
        print("Reader thread: NOT RUNNING — restarting", flush=True)
        deck.run_read_thread = True
        deck._setup_reader(deck._read)

    threading.Thread(target=_hang_watchdog, args=(deck,), daemon=True).start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
    finally:
        deck.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
