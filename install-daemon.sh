#!/bin/bash
# Install knob-controller as a LaunchDaemon (runs as root for HID access)
set -e

PLIST="com.dymir.knob-controller"
SRC="$(dirname "$0")/$PLIST.plist"
DEST="/Library/LaunchDaemons/$PLIST.plist"
OLD_AGENT="$HOME/Library/LaunchAgents/$PLIST.plist"

# Unload old LaunchAgent if it exists
if [ -f "$OLD_AGENT" ]; then
    launchctl unload "$OLD_AGENT" 2>/dev/null || true
    echo "Unloaded old LaunchAgent"
fi

# Install as LaunchDaemon (requires sudo)
sudo cp "$SRC" "$DEST"
sudo chown root:wheel "$DEST"
sudo chmod 644 "$DEST"
sudo launchctl load "$DEST"

echo "Installed and started $PLIST as LaunchDaemon (root)"
echo "Logs: /tmp/knob-controller.log"
echo "Errors: /tmp/knob-controller.err"
