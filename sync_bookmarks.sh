#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
MODE="both"
APPLY=1
RUN_TESTS=0
EXTRA_ARGS=()

usage() {
  cat <<'USAGE'
Chrome Safari Bookmarks Sync

Usage:
  ./sync_bookmarks.sh [options]

Options:
  --dry-run                 Preview changes without writing files.
  --apply                   Write changes. This is the default.
  --mode MODE               both, chrome-to-safari, or safari-to-chrome.
  --dedup-policy POLICY     conservative (default) or tracking.
  --backup-retention COUNT  Keep this many backups per browser file.
  --allow-running-browsers  Bypass the destination-browser safety check.
  --include-active-bookmarks  Include javascript: and data: bookmarks.
  --run-tests               Run the built-in unit tests before syncing.
  --skip-tests              Do not run tests. Kept for compatibility.
  --install-agent           Install the automatic LaunchAgent after this run.
  --                         Pass the rest of the arguments to chrome_to_safari.py.

Examples:
  ./sync_bookmarks.sh
  ./sync_bookmarks.sh --dry-run
  ./sync_bookmarks.sh --mode safari-to-chrome
USAGE
}

INSTALL_AGENT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      APPLY=0
      shift
      ;;
    --apply)
      APPLY=1
      shift
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --skip-tests)
      RUN_TESTS=0
      shift
      ;;
    --run-tests)
      RUN_TESTS=1
      shift
      ;;
    --install-agent)
      INSTALL_AGENT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

case "$MODE" in
  both|chrome-to-safari|safari-to-chrome) ;;
  *)
    echo "Invalid mode: $MODE" >&2
    exit 2
    ;;
esac

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found or not executable: $PYTHON" >&2
  exit 2
fi

cd "$SCRIPT_DIR"

echo "==> Preflight"
"$PYTHON" --version

if [[ "$RUN_TESTS" -eq 1 ]]; then
  echo "==> Running tests"
  "$PYTHON" -m unittest discover -s tests -v
fi

SYNC_ARGS=(--mode "$MODE" --preview-limit 0)
if [[ "$APPLY" -eq 1 ]]; then
  SYNC_ARGS+=(--apply)
fi
if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
  SYNC_ARGS+=("${EXTRA_ARGS[@]}")
fi

echo "==> Syncing bookmarks"
"$PYTHON" "$SCRIPT_DIR/chrome_to_safari.py" "${SYNC_ARGS[@]}"

if [[ "$INSTALL_AGENT" -eq 1 ]]; then
  echo "==> Installing LaunchAgent"
  "$SCRIPT_DIR/install_launch_agent.sh"
fi

echo "==> Done"
