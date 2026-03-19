#!/usr/bin/env bash
# Session Journal Generator
# Called by Claude Code Stop hook — extracts transcript, spawns background summarizer
set -uo pipefail

JOURNALS_DIR="$HOME/.claude/journals"

# Read hook input from stdin
HOOK_INPUT=""
if [ ! -t 0 ]; then
  HOOK_INPUT=$(cat)
fi

# Extract session_id from hook input
SESSION_ID=$(echo "$HOOK_INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || true)

if [ -z "$SESSION_ID" ]; then
  echo '{}'
  exit 0
fi

# Dedup: skip if this session was already journaled
DEDUP_DIR="${JOURNALS_DIR}/.pending"
mkdir -p "$DEDUP_DIR"
DEDUP_FILE="${DEDUP_DIR}/.done_${SESSION_ID}"
if [ -f "$DEDUP_FILE" ]; then
  echo '{}'
  exit 0
fi
touch "$DEDUP_FILE"

# Try to extract transcript from JSONL (primary source)
TRANSCRIPT_FILE="${DEDUP_DIR}/${SESSION_ID}.txt"
python3 "${JOURNALS_DIR}/extract-transcript.py" "$SESSION_ID" 20000 > "$TRANSCRIPT_FILE" 2>/dev/null || true

# Fallback to tmux if JSONL extraction failed or too short
if [ ! -s "$TRANSCRIPT_FILE" ] || [ "$(wc -c < "$TRANSCRIPT_FILE")" -lt 100 ]; then
  TMUX_SESS="${CLAUDE_TMUX_SESSION:-}"
  if [ -n "$TMUX_SESS" ] && tmux has-session -t "$TMUX_SESS" 2>/dev/null; then
    tmux capture-pane -t "$TMUX_SESS" -p -S -5000 > "$TRANSCRIPT_FILE" 2>/dev/null || true
  fi
fi

# Still nothing? Skip
if [ ! -s "$TRANSCRIPT_FILE" ] || [ "$(wc -c < "$TRANSCRIPT_FILE")" -lt 100 ]; then
  rm -f "$TRANSCRIPT_FILE"
  echo '{}'
  exit 0
fi

# Spawn async summarizer
NOW=$(TZ=Asia/Shanghai date +"%Y-%m-%d_%H%M")
nohup bash "${JOURNALS_DIR}/journal-worker.sh" "$TRANSCRIPT_FILE" "$SESSION_ID" "$NOW" \
  >> "${DEDUP_DIR}/worker.log" 2>&1 &

echo '{}'
