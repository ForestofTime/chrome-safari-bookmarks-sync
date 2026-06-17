#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.chrome-to-safari-bookmarks"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl unload "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"

echo "Removed $LABEL"
