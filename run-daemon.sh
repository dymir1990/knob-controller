#!/bin/bash
# Wrapper for LaunchDaemon — runs from /opt but reads source from user dir
cd /Users/dymirtatem/Desktop/Projects/knob-controller
exec /opt/homebrew/bin/node index.js
