#!/usr/bin/env python3
"""
Knob Controller - StreamDock N3 → Mac volume, Nest Hub volume, media controls
Uses the official StreamDock Python SDK for reliable HID communication.

Knob mapping:
  KNOB_3 (big/top)      → Mac system volume (press to mute)
  KNOB_2 (bottom-right)  → Nest Hub volume (press to mute)
  KNOB_1 (bottom-left)   → Available for future use
"""

import os
import sys
import time
import signal
import subprocess
import threading
from urllib import request as urllib_request
import json
import ssl

# Add SDK to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "StreamDock-Device-SDK", "Python-SDK", "src"))

from StreamDock.DeviceManager import DeviceManager
from StreamDock.InputTypes import EventType, KnobId, Direction

# ─── Config ───
NEST_HUB_IP = "10.0.0.213"
SYSTEM_VOL_STEP = 5
CAST_VOL_STEP = 5  # percentage

TG_BOT_TOKEN = "8274257522:AAEphv-D7mhSx8VmPV6T9k4mm-Im2ofX6G4"
TG_CHAT_ID = "8228433205"


def send_telegram(message):
    try:
        data = json.dumps({"chat_id": TG_CHAT_ID, "text": f"\u26a0\ufe0f Knob Controller: {message}"}).encode()
        req = urllib_request.Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        ctx = ssl.create_default_context()
        urllib_request.urlopen(req, context=ctx, timeout=5)
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)


# ─── System Volume ───
def get_system_volume():
    try:
        out = subprocess.check_output(
            ["osascript", "-e", "output volume of (get volume settings)"], text=True
        )
        return int(out.strip())
    except Exception:
        return 50


def set_system_volume(vol):
    vol = max(0, min(100, vol))
    subprocess.run(["osascript", "-e", f"set volume output volume {vol}"], check=False)
    return vol


def toggle_system_mute():
    try:
        out = subprocess.check_output(
            ["osascript", "-e", "output muted of (get volume settings)"], text=True
        )
        muted = out.strip() == "true"
        subprocess.run(["osascript", "-e", f"set volume output muted {not muted}"], check=False)
        log(f"System {'unmuted' if muted else 'muted'}")
    except Exception as e:
        log(f"Mute error: {e}")


# ─── Nest Hub Volume (via castv2 - use osascript for simplicity) ───
class NestHubController:
    def __init__(self, ip):
        self.ip = ip
        self.volume = 75
        self._init_volume()

    def _init_volume(self):
        """Get current volume via catt if available"""
        try:
            out = subprocess.check_output(
                ["catt", "-d", self.ip, "status"], text=True, timeout=5, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if "volume" in line.lower():
                    vol = int("".join(c for c in line.split(":")[-1] if c.isdigit()))
                    self.volume = vol
                    log(f"Nest Hub volume: {self.volume}%")
                    return
        except Exception:
            log(f"Nest Hub at {self.ip} (starting at {self.volume}%)")

    def adjust(self, delta):
        self.volume = max(0, min(100, self.volume + delta))
        try:
            subprocess.run(
                ["catt", "-d", self.ip, "volume", str(self.volume)],
                timeout=5, check=False, capture_output=True,
            )
            log(f"Nest Hub volume: {self.volume}%")
        except Exception as e:
            log(f"Nest Hub error: {e}")

    def toggle_mute(self):
        # Toggle by setting to 0 or restoring
        if self.volume > 0:
            self._prev_volume = self.volume
            self.adjust(-self.volume)
            log("Nest Hub muted")
        else:
            self.adjust(getattr(self, "_prev_volume", 50))
            log("Nest Hub unmuted")


# ─── Main ───
def main():
    log("Knob Controller (Python SDK) starting...")

    # Find device with retries
    device = None
    for attempt in range(30):
        dm = DeviceManager()
        devices = dm.enumerate()
        if devices:
            device = devices[0]
            break
        log(f"Waiting for StreamDock N3... (attempt {attempt + 1}/30)")
        time.sleep(2)

    if not device:
        msg = "StreamDock N3 not found after 60s"
        log(msg)
        send_telegram(msg)
        sys.exit(1)

    device.open()
    device.init()
    log(f"Found StreamDock N3 at {device.path}")

    nest_hub = NestHubController(NEST_HUB_IP)

    def on_event(dev, event):
        try:
            if event.event_type == EventType.KNOB_ROTATE:
                if event.knob_id == KnobId.KNOB_3:  # Big knob
                    delta = SYSTEM_VOL_STEP if event.direction == Direction.RIGHT else -SYSTEM_VOL_STEP
                    current = get_system_volume()
                    new_vol = set_system_volume(current + delta)
                    log(f"System volume: {new_vol}%")

                elif event.knob_id == KnobId.KNOB_2:  # Bottom-right
                    delta = CAST_VOL_STEP if event.direction == Direction.RIGHT else -CAST_VOL_STEP
                    nest_hub.adjust(delta)

                elif event.knob_id == KnobId.KNOB_1:  # Bottom-left
                    pass  # Future use

            elif event.event_type == EventType.KNOB_PRESS and event.state == 1:
                if event.knob_id == KnobId.KNOB_3:
                    toggle_system_mute()
                elif event.knob_id == KnobId.KNOB_2:
                    nest_hub.toggle_mute()
                elif event.knob_id == KnobId.KNOB_1:
                    pass  # Future use

        except Exception as e:
            log(f"Event handler error: {e}")

    device.set_key_callback(on_event)

    log("\nKnob Controller running!")
    log("  Big knob       -> Mac system volume (press to mute)")
    log("  Bottom-right   -> Nest Hub volume (press to mute)")
    log("  Bottom-left    -> (available)\n")

    def shutdown(sig=None, frame=None):
        log("\nShutting down...")
        device.set_key_callback(None)
        time.sleep(0.2)
        device.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(0.1)
    except Exception as e:
        msg = f"Crash: {e}"
        log(msg)
        send_telegram(msg)
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"Fatal crash: {e}")
        raise