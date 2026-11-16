#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-run}"
case "$MODE" in run|--verify|--debug) ;; *) echo "Usage: $0 [--verify|--debug]" >&2; exit 2 ;; esac
cd "$ROOT_DIR"
# SIGTERM lets the native engine finish its retained audio before rebuilding.
pkill -x CaptionFlowGPUI >/dev/null 2>&1 || true
for ((attempt=0; attempt<60; attempt++)); do
  if ! pgrep -x CaptionFlowGPUI >/dev/null; then break; fi
  sleep 1
done
if pgrep -x CaptionFlowGPUI >/dev/null; then echo "CaptionFlow is still finishing its session." >&2; exit 1; fi
CONFIGURATION="${CONFIGURATION:-debug}" ./scripts/build-app.sh
APP="$ROOT_DIR/dist/CaptionFlow.app"
if [[ "$MODE" == --debug ]]; then exec /usr/bin/lldb -- "$APP/Contents/MacOS/CaptionFlowGPUI"; fi
/usr/bin/open -n --stdout "$ROOT_DIR/dist/gpui.log" --stderr "$ROOT_DIR/dist/gpui.log" "$APP"
if [[ "$MODE" == --verify ]]; then sleep 2; pgrep -x CaptionFlowGPUI >/dev/null; echo "CaptionFlow GPUI is running."; fi
