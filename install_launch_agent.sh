#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
LABEL="com.local.chrome-to-safari-bookmarks"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CHROME_ROOT="$HOME/Library/Application Support/Google/Chrome"
SAFARI_BOOKMARKS="$HOME/Library/Safari/Bookmarks.plist"
OUT_LOG="$HOME/Library/Logs/chrome-to-safari-bookmarks.log"
ERR_LOG="$HOME/Library/Logs/chrome-to-safari-bookmarks.err.log"

umask 077
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
touch "$OUT_LOG" "$ERR_LOG"
chmod 600 "$OUT_LOG" "$ERR_LOG"

LABEL="$LABEL" PLIST="$PLIST" PYTHON="$PYTHON" SCRIPT_DIR="$SCRIPT_DIR" \
CHROME_ROOT="$CHROME_ROOT" SAFARI_BOOKMARKS="$SAFARI_BOOKMARKS" \
OUT_LOG="$OUT_LOG" ERR_LOG="$ERR_LOG" "$PYTHON" <<'PY'
import os
import plistlib
from pathlib import Path

chrome_root = Path(os.environ["CHROME_ROOT"])
watch_paths = sorted(
    str(path)
    for path in chrome_root.glob("*/Bookmarks")
    if path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
)
watch_paths.append(os.environ["SAFARI_BOOKMARKS"])

data = {
    "Label": os.environ["LABEL"],
    "ProgramArguments": [
        os.environ["PYTHON"],
        str(Path(os.environ["SCRIPT_DIR"]) / "chrome_to_safari.py"),
        "--apply",
        "--preview-limit",
        "0",
        "--quiet",
        "--lock-timeout",
        "30",
    ],
    "RunAtLoad": True,
    "StartInterval": 300,
    "WatchPaths": watch_paths,
    "ProcessType": "Background",
    "LowPriorityIO": True,
    "ThrottleInterval": 10,
    "StandardOutPath": os.environ["OUT_LOG"],
    "StandardErrorPath": os.environ["ERR_LOG"],
}
with Path(os.environ["PLIST"]).open("wb") as handle:
    plistlib.dump(data, handle, fmt=plistlib.FMT_XML, sort_keys=False)
PY

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST"

echo "Installed and loaded $LABEL"
echo "LaunchAgent: $PLIST"
echo "Logs: $OUT_LOG"
