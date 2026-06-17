#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
LABEL="com.local.chrome-to-safari-bookmarks"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CHROME_BOOKMARKS="$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks"
SAFARI_BOOKMARKS="$HOME/Library/Safari/Bookmarks.plist"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SCRIPT_DIR/chrome_to_safari.py</string>
    <string>--apply</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>WatchPaths</key>
  <array>
    <string>$CHROME_BOOKMARKS</string>
    <string>$SAFARI_BOOKMARKS</string>
  </array>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/chrome-to-safari-bookmarks.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/chrome-to-safari-bookmarks.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"

echo "Installed and loaded $LABEL"
echo "LaunchAgent: $PLIST"
echo "Logs: $HOME/Library/Logs/chrome-to-safari-bookmarks.log"
