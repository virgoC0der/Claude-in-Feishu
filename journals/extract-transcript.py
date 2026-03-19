#!/usr/bin/env python3
"""Extract a readable transcript from a Claude Code session JSONL file.

Usage: python3 extract-transcript.py <session_id_or_jsonl_path> [max_chars]

Outputs a condensed text transcript suitable for summarization.
Default max_chars=20000 to stay within Haiku's sweet spot.
"""
import sys, json, os, glob

def find_jsonl(session_id):
    """Find JSONL file by session ID or path."""
    if os.path.isfile(session_id):
        return session_id
    # Search in projects dir
    pattern = os.path.expanduser(f"~/.claude/projects/-Users-*/{session_id}.jsonl")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    # Try partial match
    pattern = os.path.expanduser(f"~/.claude/projects/-Users-*/{session_id}*.jsonl")
    matches = glob.glob(pattern)
    if matches:
        return sorted(matches, key=os.path.getmtime, reverse=True)[0]
    return None

def extract_text(content):
    """Extract readable text from message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Show tool name + key input (truncated)
                    if name == "Bash":
                        cmd = inp.get("command", "")[:200]
                        parts.append(f"[Tool: Bash] {cmd}")
                    elif name in ("Read", "Write", "Edit"):
                        path = inp.get("file_path", "")
                        parts.append(f"[Tool: {name}] {path}")
                    elif name == "Agent":
                        desc = inp.get("description", "")
                        parts.append(f"[Tool: Agent] {desc}")
                    else:
                        parts.append(f"[Tool: {name}]")
                elif block.get("type") == "tool_result":
                    # Skip verbose tool results
                    pass
        return "\n".join(parts)
    return ""

def extract_transcript(jsonl_path, max_chars=20000):
    """Parse JSONL and produce readable transcript."""
    lines = []
    with open(jsonl_path) as f:
        for raw in f:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = entry.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            content = entry.get("message", {}).get("content", "")
            text = extract_text(content).strip()
            if not text:
                continue

            role = "User" if msg_type == "user" else "Claude"
            lines.append(f"[{role}]\n{text}\n")

    transcript = "\n".join(lines)

    # Truncate smartly: keep beginning and end
    if len(transcript) > max_chars:
        half = max_chars // 2
        transcript = (
            transcript[:half]
            + "\n\n... [middle truncated] ...\n\n"
            + transcript[-half:]
        )

    return transcript

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: extract-transcript.py <session_id_or_path> [max_chars]", file=sys.stderr)
        sys.exit(1)

    path = find_jsonl(sys.argv[1])
    if not path:
        print(f"Session not found: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    max_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    print(extract_transcript(path, max_chars))
