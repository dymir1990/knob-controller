#!/usr/bin/env python3
"""
Knob Controller Daemon
Listens for keyboard events from VSDinside macro pad knobs and controls:
  - Big knob: Mac system volume
  - Bottom-right small knob: Google Nest Hub (Living Room Display) volume
"""
import subprocess
import threading
import time
import Quartz
from Quartz import (
    CGEventTapCreate, CGEventMaskBit, CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent, CFRunLoopAddSource, CGEventTapEnable,
    CGEventGetIntegerValueField, CGEventGetFlags,
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
    kCGEventKeyDown, kCGEventKeyUp, kCGKeyboardEventKeycode,
    kCFRunLoopCommonModes, kCFRunLoopDefaultMode,
)

# --- Configuration ---
# These keycodes will be set based on how VSD Craft configures the knobs.
# Default: F13-F16 (keycodes 105, 107, 113, 106) are good candidates since they
# don't conflict with anything on Mac.

# Big knob (system volume)
BIG_KNOB_CW_KEYCODE = 105    # F13 - clockwise = volume up
BIG_KNOB_CCW_KEYCODE = 107   # F15 - counter-clockwise = volume down

# Small bottom-right knob (Google Nest Hub volume)
SMALL_KNOB_CW_KEYCODE = 113  # F16? - placeholder, will update after VSD config
SMALL_KNOB_CCW_KEYCODE = 106 # F14? - placeholder, will update after VSD config

# Volume step sizes
SYSTEM_VOLUME_STEP = 5       # percentage per click
CAST_VOLUME_STEP = 0.05      # 0.0 to 1.0 scale, 5% per click

# Google Nest Hub
NEST_HUB_NAME = "Living Room Display"
NEST_HUB_IP = "10.0.0.213"

# --- System Volume Control ---
def get_system_volume():
    """Get current macOS system volume (0-100)."""
    result = subprocess.run(
        ["osascript", "-e", "output volume of (get volume settings)"],
        capture_output=True, text=True
    )
    return int(result.stdout.strip())

def set_system_volume(vol):
    """Set macOS system volume (0-100)."""
    vol = max(0, min(100, vol))
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {vol}"],
        capture_output=True
    )
    return vol

def adjust_system_volume(delta):
    """Adjust system volume by delta percentage."""
    current = get_system_volume()
    new_vol = set_system_volume(current + delta)
    print(f"System volume: {new_vol}%")

# --- Google Nest Hub Volume Control ---
class NestHubController:
    def __init__(self, host, name):
        self.host = host
        self.name = name
        self.cast = None
        self._connect()

    def _connect(self):
        """Connect to the Nest Hub via pychromecast."""
        try:
            import pychromecast
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[self.name]
            )
            if chromecasts:
                self.cast = chromecasts[0]
                self.cast.wait()
                print(f"Connected to {self.name} at {self.host}")
            else:
                # Try by IP
                self.cast = pychromecast.Chromecast(self.host)
                self.cast.wait()
                print(f"Connected to Chromecast at {self.host}")
            browser.stop_discovery()
        except Exception as e:
            print(f"Failed to connect to Nest Hub: {e}")
            self.cast = None

    def adjust_volume(self, delta):
        """Adjust Nest Hub volume by delta (float, -1.0 to 1.0)."""
        if not self.cast:
            print("Nest Hub not connected, attempting reconnect...")
            self._connect()
            if not self.cast:
                return

        try:
            current = self.cast.status.volume_level
            new_vol = max(0.0, min(1.0, current + delta))
            self.cast.set_volume(new_vol)
            print(f"Nest Hub volume: {int(new_vol * 100)}%")
        except Exception as e:
            print(f"Error adjusting Nest Hub volume: {e}")
            self.cast = None  # Force reconnect on next attempt


# --- Event Handler ---
def run_event_loop(nest_hub):
    """Set up CGEvent tap and run the event loop."""

    handled_keycodes = {
        BIG_KNOB_CW_KEYCODE, BIG_KNOB_CCW_KEYCODE,
        SMALL_KNOB_CW_KEYCODE, SMALL_KNOB_CCW_KEYCODE,
    }

    def callback(proxy, event_type, event, refcon):
        if event_type == kCGEventKeyDown:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

            if keycode in handled_keycodes:
                if keycode == BIG_KNOB_CW_KEYCODE:
                    adjust_system_volume(SYSTEM_VOLUME_STEP)
                elif keycode == BIG_KNOB_CCW_KEYCODE:
                    adjust_system_volume(-SYSTEM_VOLUME_STEP)
                elif keycode == SMALL_KNOB_CW_KEYCODE:
                    nest_hub.adjust_volume(CAST_VOLUME_STEP)
                elif keycode == SMALL_KNOB_CCW_KEYCODE:
                    nest_hub.adjust_volume(-CAST_VOLUME_STEP)

        return event

    mask = CGEventMaskBit(kCGEventKeyDown)

    tap = CGEventTapCreate(
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )

    if tap is None:
        print("ERROR: Could not create event tap.")
        print("Go to System Settings > Privacy & Security > Accessibility")
        print("and grant access to Terminal (or your terminal app).")
        return

    source = CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = CFRunLoopGetCurrent()
    CFRunLoopAddSource(loop, source, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)

    print("\nKnob Controller running!")
    print(f"  Big knob    -> System volume (keycodes {BIG_KNOB_CW_KEYCODE}/{BIG_KNOB_CCW_KEYCODE})")
    print(f"  Small knob  -> Nest Hub volume (keycodes {SMALL_KNOB_CW_KEYCODE}/{SMALL_KNOB_CCW_KEYCODE})")
    print("Press Ctrl+C to stop.\n")

    Quartz.CFRunLoopRun()


def main():
    print("Knob Controller Daemon starting...")
    print(f"Connecting to {NEST_HUB_NAME}...")

    nest_hub = NestHubController(NEST_HUB_IP, NEST_HUB_NAME)

    try:
        run_event_loop(nest_hub)
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
