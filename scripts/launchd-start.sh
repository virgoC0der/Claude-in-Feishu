#!/usr/bin/env bash
# launchd wrapper — idempotent start
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$HOME/.claude-to-im/runtime/bridge.pid"

# If already running, exit 0 so launchd doesn't retry
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  exit 0
fi

exec bash "$SKILL_DIR/scripts/daemon.sh" start
