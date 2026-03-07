#!/usr/bin/env python3
"""
Initialize StreamDock N3 — wake screen, set brightness, enable input.
Run this before index.js to ensure the device sends knob/button events.
"""
import sys
sys.path.insert(0, "/Users/dymirtatem/Desktop/Projects/knob-controller/StreamDock-Device-SDK/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager

def main():
    manager = DeviceManager()
    decks = manager.enumerate()

    if not decks:
        print("No StreamDock devices found")
        sys.exit(1)

    deck = decks[0]
    print(f"Found: VID={hex(deck.vendor_id)}, PID={hex(deck.product_id)}, path={deck.path}")

    print("Opening device...")
    deck.open()

    print("Initializing (wake, brightness, clear, refresh)...")
    deck.init()
    print(f"Firmware: {deck.firmware_version}")
    print("Device initialized")

    # Close cleanly (releases HID so index.js can grab it)
    deck.close()
    print("Device closed — ready for index.js")

if __name__ == "__main__":
    main()
