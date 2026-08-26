#!/bin/bash
# Installs the agent as a background service that starts at login.
#
# launchd, not a login item and not a cron job: launchd restarts it if it dies,
# throttles it to the background priority band, and stops it cleanly at logout.
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-$(command -v python3)}"
LABEL="co.soclose.sosforge.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -f "$HOME/.config/sosforge/agent.json" ]; then
  echo "No configuration yet. Run this first:"
  echo "  PYTHONPATH=$AGENT_DIR $PYTHON -m sosforge_agent setup"
  exit 2
fi

if ! "$PYTHON" -c "import websockets" 2>/dev/null; then
  echo "The 'websockets' package is missing for $PYTHON."
  echo "  $PYTHON -m pip install websockets"
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__AGENT_DIR__|$AGENT_DIR|g" \
    -e "s|__HOME__|$HOME|g" \
    "$AGENT_DIR/$LABEL.plist" > "$PLIST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed. It is running now and will start at every login."
echo "  logs     tail -f ~/Library/Logs/sosforge-agent.log"
echo "  stop     launchctl bootout gui/$(id -u)/$LABEL"
echo "  status   launchctl print gui/$(id -u)/$LABEL | head -20"
