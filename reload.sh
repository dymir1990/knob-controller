#!/bin/bash
# Reload knob-controller LaunchDaemon.
#
# Run as YOURSELF (not sudo):   ./reload.sh   [--force]
#
# Why not sudo: macOS TCC blocks root from reading ~/Desktop ("Operation not
# permitted"), so this script stages files into /private/tmp as you, then runs
# only the privileged part as root via the macOS admin password dialog.
set -euo pipefail

LABEL="com.dymir.knob-controller"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="/opt/knob-controller"
LOG_DIR="/Library/Logs/knob-controller"
STAGE="$(mktemp -d /private/tmp/knob-controller-stage.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

if [ "$(id -u)" = "0" ]; then
    echo "Run as your user, not root/sudo (root can't read ~/Desktop under TCC)." >&2
    exit 1
fi

# 1. Refuse to deploy broken code.
python3 -m py_compile "$SRC_DIR/daemon.py"
python3 -m json.tool "$SRC_DIR/buttons.json" > /dev/null
plutil -lint -s "$SRC_DIR/$LABEL.plist"

# 2. Refuse to overwrite a newer deployed daemon (2026-10: /opt held fixes the
#    Desktop source lacked; a blind rsync would have reverted them).
if [ "${1:-}" != "--force" ] && [ -f "$DEST/daemon.py" ] \
    && ! cmp -s "$DEST/daemon.py" "$SRC_DIR/daemon.py" \
    && [ "$DEST/daemon.py" -nt "$SRC_DIR/daemon.py" ]; then
    echo "ABORT: $DEST/daemon.py is newer than the source and differs." >&2
    echo "  Inspect: diff $DEST/daemon.py $SRC_DIR/daemon.py" >&2
    echo "  Pull it back (cp $DEST/daemon.py $SRC_DIR/) or rerun with --force." >&2
    exit 1
fi

# 3. Stage everything root needs outside TCC-protected Desktop.
cp "$SRC_DIR/daemon.py" "$SRC_DIR/buttons.json" "$SRC_DIR/gen_icons.py" \
   "$SRC_DIR/package.json" "$SRC_DIR/$LABEL.plist" "$STAGE/"
rsync -a "$SRC_DIR/icons" "$SRC_DIR/StreamDock-Device-SDK" "$STAGE/"
chmod -R a+rX "$STAGE"

# 4. Privileged part: install, then restart. bootout is async, so wait for the
#    job to be gone before bootstrap (bootstrapping too early silently fails).
ROOT_SCRIPT="$STAGE/root-step.sh"
cat > "$ROOT_SCRIPT" <<EOF
set -e
mkdir -p "$DEST" "$LOG_DIR"
cp "$STAGE/daemon.py" "$STAGE/buttons.json" "$STAGE/gen_icons.py" "$STAGE/package.json" "$DEST/"
rsync -a "$STAGE/icons" "$STAGE/StreamDock-Device-SDK" "$DEST/"
ln -sf /opt/homebrew/lib/libhidapi.0.dylib \
    "$DEST/StreamDock-Device-SDK/Python-SDK/src/StreamDock/Transport/TransportDLL/libhidapi.0.dylib"
cp "$STAGE/$LABEL.plist" "$PLIST"
chown root:wheel "$PLIST"; chmod 644 "$PLIST"
: > "$LOG_DIR/out.log"; : > "$LOG_DIR/err.log"
launchctl bootout system/$LABEL 2>/dev/null || true
for i in \$(seq 1 20); do launchctl print system/$LABEL >/dev/null 2>&1 || break; sleep 0.5; done
launchctl bootstrap system "$PLIST"
EOF

if [ -t 0 ]; then
    sudo bash "$ROOT_SCRIPT"
else
    osascript -e "do shell script \"bash '$ROOT_SCRIPT'\" with administrator privileges with prompt \"Deploy and restart the knob controller\""
fi

# 5. Verify it came up and found the device.
sleep 8
echo "=== LOG ==="; tail -25 "$LOG_DIR/out.log"
echo "=== ERR ==="; tail -10 "$LOG_DIR/err.log"
if grep -q "Reader thread: ALIVE" "$LOG_DIR/out.log"; then
    echo "OK: knob controller running"
else
    echo "WARNING: reader thread not confirmed — check logs above (unplug/replug USB if no device)" >&2
    exit 1
fi
