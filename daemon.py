#!/usr/bin/env python3
"""
Knob Controller Daemon (Python + StreamDock SDK)

Uses the proprietary libtransport C library for HID access (bypasses macOS Tahoe
IOKit restrictions that block node-hid and standard hidapi).

Big knob (KNOB_3)       -> Mac system volume (press to mute)
Bottom-right (KNOB_2)   -> Nest Hub volume (press to mute)
Bottom-left (KNOB_1)    -> A15 volume (press to mute)
"""
import sys
import os
import signal
import subprocess
import threading
import time
import concurrent.futures

VENV_SITE = "/Users/dymirtatem/Desktop/Projects/knob-controller/venv/lib/python3.14/site-packages"
SDK_PATH = os.path.join(os.path.dirname(__file__), "StreamDock-Device-SDK/Python-SDK/src")
sys.path.insert(0, VENV_SITE)
sys.path.insert(0, SDK_PATH)

from StreamDock.DeviceManager import DeviceManager
from StreamDock.InputTypes import EventType, KnobId, Direction

# ─── Config ───
NEST_HUB_NAME = "Living Room Display"
SYSTEM_VOL_STEP = 5
CAST_VOL_STEP = 5  # percentage

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


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


# ─── A15 Volume (AirPlay via pyatv) ───
def _a15_set_volume(target):
    try:
        import asyncio
        from pyatv import scan, connect
        loop = asyncio.new_event_loop()
        devs = loop.run_until_complete(scan(loop, timeout=5))
        a15 = [d for d in devs if "A15" in d.name]
        if not a15:
            print("A15: offline", flush=True)
            return
        atv = loop.run_until_complete(connect(a15[0], loop))
        try:
            target = max(0, min(100, target))
            loop.run_until_complete(atv.audio.set_volume(target))
            print(f"A15: {target}%", flush=True)
        finally:
            atv.close()
            loop.close()
    except Exception as e:
        print(f"A15 error: {e}", flush=True)

def _a15_get_volume():
    try:
        import asyncio
        from pyatv import scan, connect
        loop = asyncio.new_event_loop()
        devs = loop.run_until_complete(scan(loop, timeout=5))
        a15 = [d for d in devs if "A15" in d.name]
        if not a15:
            return None
        atv = loop.run_until_complete(connect(a15[0], loop))
        try:
            return int(atv.audio.volume)
        finally:
            atv.close()
            loop.close()
    except Exception:
        return None

def a15_adjust(delta):
    def _do():
        vol = _a15_get_volume()
        if vol is not None:
            _a15_set_volume(vol + delta)
        else:
            print("A15: offline", flush=True)
    _executor.submit(_do)

def a15_mute():
    _executor.submit(_a15_set_volume, 0)


# ─── Event Handler ───
def on_event(event):
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
            a15_mute()


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

    # Pre-connect to Hub in background
    _executor.submit(_get_hub)

    print("\nKnob Controller running!", flush=True)
    print("  Big knob       -> Mac volume (press to mute)", flush=True)
    print("  Bottom-right   -> Nest Hub volume (press to mute)", flush=True)
    print("  Bottom-left    -> A15 volume (press to mute)", flush=True)

    # Read loop
    try:
        while True:
            data = deck.read()
            if data is not None and len(data) >= 11:
                event = deck.decode_input_event(data[9], data[10])
                if event.event_type != EventType.UNKNOWN:
                    on_event(event)
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
    finally:
        deck.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
