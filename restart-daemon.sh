#!/bin/bash
# Restart the knob controller LaunchDaemon
# Requires: sudo visudo entry (see below) or manual password entry
#
# To enable passwordless restart, run:
#   sudo visudo -f /etc/sudoers.d/knob-controller
# And add:
#   dymirtatem ALL=(root) NOPASSWD: /bin/launchctl load /Library/LaunchDaemons/com.dymir.knob-controller.plist
#   dymirtatem ALL=(root) NOPASSWD: /bin/launchctl unload /Library/LaunchDaemons/com.dymir.knob-controller.plist

PLIST="/Library/LaunchDaemons/com.dymir.knob-controller.plist"

sudo launchctl unload "$PLIST" 2>/dev/null
sleep 1
sudo launchctl load "$PLIST"
sleep 3
echo "=== Last 20 log lines ==="
tail -20 /tmp/knob-controller.log
