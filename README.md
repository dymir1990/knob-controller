# StreamDock N3 Knob Controller

Controls Mac system volume, Google Nest Hub, and A15 speaker using a VSDinside StreamDock N3 macro pad.

## Knob Mapping

| Knob | Function |
|------|----------|
| Big knob (top) | Mac system volume |
| Bottom-right knob | Google Nest Hub (Living Room Display) volume |
| Bottom-left knob | A15 speaker volume |

## Architecture

- Runs as a **macOS LaunchDaemon** (requires root for HID access)
- Uses the **StreamDock Python SDK's proprietary `libtransport`** for HID communication, bypassing macOS Tahoe's IOKit restrictions that block standard `hidapi`/`node-hid` access
- `daemon.py` is the main entry point: reads knob events via the StreamDock SDK and dispatches volume changes
- `vol` is a compiled helper for controlling Mac system volume
- Google Nest Hub and A15 volume controlled via network commands

## Setup

1. Clone this repo
2. Run `reload.sh` — it copies the project to `/opt/knob-controller` and bootstraps the LaunchDaemon:
   ```bash
   ./reload.sh
   ```
   This will:
   - Copy all necessary files to `/opt/knob-controller`
   - Install the StreamDock SDK's `libtransport` shared library
   - Load (or reload) the `com.dymir.knob-controller` LaunchDaemon

3. The daemon starts automatically on boot. To manually restart:
   ```bash
   ./restart-daemon.sh
   ```

## Files

| File | Purpose |
|------|---------|
| `daemon.py` | Main daemon — reads HID events, dispatches volume changes |
| `init_device.py` | Initializes the StreamDock N3 device connection |
| `knob_controller.py` | Knob event handling and volume control logic |
| `knob_daemon.py` | Earlier daemon implementation |
| `vol` | Compiled Mac volume control binary |
| `index.js` | Node.js HID event reader (alternative approach) |
| `button-test.js` | Button/knob event testing script |
| `reload.sh` | Deploy to /opt and reload LaunchDaemon |
| `install-daemon.sh` | Initial daemon installation |
| `run-daemon.sh` | Script invoked by the LaunchDaemon |
| `com.dymir.knob-controller.plist` | LaunchDaemon plist |
