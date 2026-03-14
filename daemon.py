#!/usr/bin/env python3
"""
Knob Controller Daemon (Python + StreamDock SDK)

Uses the proprietary libtransport C library for HID access (bypasses macOS Tahoe
IOKit restrictions that block node-hid and standard hidapi).

Knobs:
  Big knob (KNOB_3)       -> Mac system volume (press to mute)
  Bottom-right (KNOB_2)   -> Nest Hub volume (press to mute)
  Bottom-left (KNOB_1)    -> A15 volume (press to mute)

Buttons (KEY_1-KEY_6):
  Configurable via buttons.json — open apps, folders, URLs
"""
import json
import sys
import os
import signal
import subprocess
import threading
import time
import concurrent.futures

SDK_PATH = os.path.join(os.path.dirname(__file__), "StreamDock-Device-SDK/Python-SDK/src")
sys.path.insert(0, SDK_PATH)

from StreamDock.DeviceManager import DeviceManager
from StreamDock.InputTypes import EventType, KnobId, Direction, ButtonKey

# ─── Config ───
NEST_HUB_NAME = "Living Room Display"
SYSTEM_VOL_STEP = 5
CAST_VOL_STEP = 5  # percentage
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
    except Exception as e:
        print(f"Button error: {e}", flush=True)

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
        hub = [c for c in casts if c.name == NEST_HUB_NAME]
        if hub:
            _hub_cast = hub[0]
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

def hub_adjust(delta):
    _executor.submit(_hub_adjust_volume, delta)

def hub_mute():
    _executor.submit(_hub_toggle_mute)


# ─── A15 Volume (Cast device — syncs with Hub) ───
_a15_cast = None
_a15_lock = threading.Lock()
_a15_muted = False
_a15_prev_vol = 0.5

def _get_a15():
    global _a15_cast
    if _a15_cast is not None:
        return _a15_cast
    try:
        import pychromecast
        casts, browser = pychromecast.get_chromecasts(timeout=10)
        for c in casts:
            c.wait(timeout=5)
        a15 = [c for c in casts if c.name == "A15"]
        if a15:
            _a15_cast = a15[0]
            print(f"Connected to A15", flush=True)
            return _a15_cast
        browser.stop_discovery()
    except Exception as e:
        print(f"A15 connect error: {e}", flush=True)
    return None

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

def a15_adjust(delta):
    _executor.submit(_a15_adjust_volume, delta)

def a15_toggle():
    _executor.submit(_a15_toggle)


# ─── Event Handler ───
def on_event(event):
    # Button press → launch app/folder/URL
    if event.event_type == EventType.BUTTON and event.state == 1:
        handle_button(event.key)
        return

    if event.event_type == EventType.KNOB_ROTATE:
        if event.knob_id == KnobId.KNOB_3:  # Big knob -> Mac volume
            delta = SYSTEM_VOL_STEP if event.direction == Direction.RIGHT else -SYSTEM_VOL_STEP
            current = get_mac_volume()
            set_mac_volume(current + delta)
        elif event.knob_id == KnobId.KNOB_2:  # Bottom-right -> Nest Hub
            delta = CAST_VOL_STEP if event.direction == Direction.RIGHT else -CAST_VOL_STEP
            hub_adjust(delta)
        elif event.knob_id == KnobId.KNOB_1:  # Bottom-left -> A15
            delta = SYSTEM_VOL_STEP if event.direction == Direction.RIGHT else -SYSTEM_VOL_STEP
            a15_adjust(delta)

    elif event.event_type == EventType.KNOB_PRESS and event.state == 1:
        if event.knob_id == KnobId.KNOB_3:
            toggle_mac_mute()
        elif event.knob_id == KnobId.KNOB_2:
            hub_mute()
        elif event.knob_id == KnobId.KNOB_1:
            a15_toggle()


# ─── Main ───
def main():
    print("Knob Controller starting...", flush=True)

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
    print("  Big knob       -> Mac volume (press to mute)", flush=True)
    print("  Bottom-right   -> Nest Hub volume (press to mute)", flush=True)
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
