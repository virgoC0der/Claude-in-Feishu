#!/usr/bin/env bash
# Background worker: summarizes a saved transcript and writes journal + index
set -uo pipefail

TRANSCRIPT_FILE="$1"
SESSION_ID="$2"
NOW="$3"

JOURNALS_DIR="$HOME/.claude/journals"
INDEX="$JOURNALS_DIR/INDEX.md"
INDEX_ARCHIVE="$JOURNALS_DIR/INDEX_ARCHIVE.md"
MAX_INDEX_LINES=200
HAIKU_MODEL="claude-haiku-4-5-20251001"

DATE_DISPLAY=$(echo "$NOW" | sed 's/_/ /' | sed 's/\([0-9]\{2\}\)\([0-9]\{2\}\)$/\1:\2/')

if [ ! -s "$TRANSCRIPT_FILE" ]; then
  rm -f "$TRANSCRIPT_FILE"
  exit 0
fi

# Build prompt file
PROMPTFILE=$(mktemp /tmp/journal_prompt.XXXXXX)
OUTFILE=$(mktemp /tmp/journal_out.XXXXXX)

cat > "$PROMPTFILE" <<'INSTRUCTION'
你是一个会话日志生成器。根据以下 Claude Code 对话记录，生成结构化日志。

严格遵循以下格式输出，每个字段占一行，不要加其他内容：

SLUG: <英文短横线slug，3-5个词>
TOPIC: <中文主题，10字以内>
SUMMARY: <做了什么，1-2句话>
KEY_FILES: <关键文件路径，逗号分隔，没有则写无>
DECISIONS: <关键决策或踩坑，1句话>
TAGS: <5-8个标签，逗号分隔>
BACKGROUND: <背景，1-2句>
PROCESS: <过程，3-6步，每步一行，用1. 2. 3.编号>
REFLECTIONS: <反思，1-3条，每条一行，用- 开头>

对话记录：
INSTRUCTION
cat "$TRANSCRIPT_FILE" >> "$PROMPTFILE"

# Wait a few seconds to avoid rate limits with concurrent claude sessions
sleep 5

# Call Haiku
/usr/local/bin/claude -p "$(cat "$PROMPTFILE")" --model "$HAIKU_MODEL" --output-format text > "$OUTFILE" 2>/dev/null || true

if [ ! -s "$OUTFILE" ]; then
  echo "[$(date)] Haiku returned empty for $TRANSCRIPT_FILE"
  rm -f "$PROMPTFILE" "$OUTFILE"
  exit 1
fi

# Parse fields
parse_field() {
  grep "^$1:" "$OUTFILE" | head -1 | sed "s/^$1: *//" || true
}

SLUG=$(parse_field "SLUG" | tr ' ' '-' | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')
TOPIC=$(parse_field "TOPIC")
SUMMARY=$(parse_field "SUMMARY")
KEY_FILES=$(parse_field "KEY_FILES")
DECISIONS=$(parse_field "DECISIONS")
TAGS=$(parse_field "TAGS")
BACKGROUND=$(parse_field "BACKGROUND")
PROCESS=$(sed -n '/^PROCESS:/,/^REFLECTIONS:/{ /^PROCESS:/d; /^REFLECTIONS:/d; p; }' "$OUTFILE" || true)
REFLECTIONS=$(sed -n '/^REFLECTIONS:/,$ { /^REFLECTIONS:/d; p; }' "$OUTFILE" || true)

[ -z "$SLUG" ] && SLUG="session-$(date +%s)"
[ -z "$TOPIC" ] && TOPIC="未命名会话"

JOURNAL_FILE="${JOURNALS_DIR}/${NOW}_${SLUG}.md"

# Write full journal
cat > "$JOURNAL_FILE" <<EOF
---
date: ${DATE_DISPLAY}
topic: ${TOPIC}
session_id: ${SESSION_ID}
tags: [${TAGS}]
key_files: [${KEY_FILES}]
---

## 背景
${BACKGROUND}

## 过程
${PROCESS}

## 决策与踩坑
${DECISIONS}

## 反思
${REFLECTIONS}

---
*Auto-generated at ${DATE_DISPLAY}*
EOF

# Prepend cover to INDEX.md (after first 3 header lines)
COVER="### ${DATE_DISPLAY} · ${SLUG}
**做了什么**: ${SUMMARY}
**关键文件**: ${KEY_FILES}
**踩坑/决策**: ${DECISIONS}
**标签**: ${TAGS}
→ [全文](${NOW}_${SLUG}.md)
"

{
  head -3 "$INDEX"
  echo "$COVER"
  tail -n +4 "$INDEX"
} > "${INDEX}.tmp" && mv "${INDEX}.tmp" "$INDEX"

# Archive old entries if INDEX too long
LINE_COUNT=$(wc -l < "$INDEX")
if [ "$LINE_COUNT" -gt "$MAX_INDEX_LINES" ]; then
  head -n "$MAX_INDEX_LINES" "$INDEX" > "${INDEX}.tmp"
  {
    echo "# Archived Journal Entries"
    echo ""
    tail -n +"$((MAX_INDEX_LINES + 1))" "$INDEX"
    [ -f "$INDEX_ARCHIVE" ] && echo "" && cat "$INDEX_ARCHIVE"
  } > "${INDEX_ARCHIVE}.tmp" && mv "${INDEX_ARCHIVE}.tmp" "$INDEX_ARCHIVE"
  mv "${INDEX}.tmp" "$INDEX"
fi

# Cleanup
rm -f "$PROMPTFILE" "$OUTFILE" "$TRANSCRIPT_FILE"
echo "[$(date)] Journal created: ${JOURNAL_FILE}"
