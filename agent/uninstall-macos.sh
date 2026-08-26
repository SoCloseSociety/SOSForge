#!/bin/bash
set -euo pipefail
LABEL="co.soclose.sosforge.agent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed. The configuration in ~/.config/sosforge/ was left alone."
