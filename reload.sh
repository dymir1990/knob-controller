#!/bin/bash
# Reload knob-controller LaunchDaemon
# Copies source to /opt/knob-controller (root-accessible) and restarts daemon
set -e

LABEL="com.dymir.knob-controller"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
SRC_DIR="/Users/dymirtatem/Desktop/Projects/knob-controller"
DEST="/opt/knob-controller"

# Sync source files to /opt (outside TCC-protected Desktop)
mkdir -p "$DEST"
rsync -a --delete \
    "$SRC_DIR/daemon.py" \
    "$SRC_DIR/package.json" \
    "$DEST/"
rsync -a "$SRC_DIR/StreamDock-Device-SDK" "$DEST/"

# Symlink libhidapi so libtransport can find it
TRANSPORT_DIR="$DEST/StreamDock-Device-SDK/Python-SDK/src/StreamDock/Transport/TransportDLL"
ln -sf /opt/homebrew/lib/libhidapi.0.dylib "$TRANSPORT_DIR/libhidapi.0.dylib" 2>/dev/null || true

# Copy fresh plist
cp "$SRC_DIR/com.dymir.knob-controller.plist" "$PLIST"
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

# Clear logs
: > /tmp/knob-controller.log
: > /tmp/knob-controller.err

# Bootout old, bootstrap new
launchctl bootout system/$LABEL 2>/dev/null || true
sleep 1
launchctl bootstrap system "$PLIST"

sleep 4
echo "=== LOG ==="
tail -20 /tmp/knob-controller.log
echo "=== ERR ==="
tail -10 /tmp/knob-controller.err
