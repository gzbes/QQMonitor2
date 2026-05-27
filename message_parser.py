"""Parse QQ NT clipboard text into structured messages, fingerprint, filter, truncate."""

import hashlib
import re
from datetime import datetime

# Pattern: sender + (full datetime | time-only)
# Sender names must not contain spaces (per deployment constraint).
_SENDER_LINE_RE = re.compile(
    r"^(.+?)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})$"
)

# Non-text message placeholders used by QQ NT.
_NON_TEXT_PLACEHOLDERS = {"[图片]", "[文件]", "[动画表情]", "[语音]", "[视频]", "[贴纸]"}


def parse_qq_messages(full_text: str) -> list[dict]:
    """Parse the raw clipboard text into a list of structured message dicts.

    Each dict has keys: sender, time (YYYY-MM-DD HH:MM:SS), content.
    Multi-line message bodies are accumulated until the next sender line.
    """
    if not full_text:
        return []

    lines = full_text.strip().splitlines()
    messages: list[dict] = []
    current: dict | None = None

    for line in lines:
        match = _SENDER_LINE_RE.match(line)
        if match:
            if current is not None and current["content"].strip():
                current["content"] = current["content"].rstrip("\n")
                messages.append(current)
            sender = match.group(1)
            time_str = match.group(2)
            if len(time_str) == 8:  # time-only, e.g. "14:32:05"
                time_str = f"{datetime.now().strftime('%Y-%m-%d')} {time_str}"
            current = {"sender": sender, "time": time_str, "content": ""}
        else:
            if current is not None and line.strip():
                current["content"] += line.strip() + "\n"

    if current is not None and current["content"].strip():
        # Strip trailing newline added during accumulation.
        current["content"] = current["content"].rstrip("\n")
        messages.append(current)

    return messages


def filter_messages(messages: list[dict]) -> list[dict]:
    """Remove system messages and non-text messages.

    System messages have no sender field (e.g. join/leave notices).
    Non-text messages contain only placeholder tokens like [图片]/[文件].
    """
    result = []
    for msg in messages:
        if not msg.get("sender"):
            continue
        if _is_non_text(msg["content"]):
            continue
        result.append(msg)
    return result


def _is_non_text(content: str) -> bool:
    """Return True if the content has no actual text, only placeholder tokens."""
    cleaned = content
    for token in _NON_TEXT_PLACEHOLDERS:
        cleaned = cleaned.replace(token, "")
    return cleaned.strip() == ""


def message_fingerprint(msg: dict) -> str:
    """MD5 fingerprint: sender | full timestamp | first 100 chars of content."""
    unique_str = f"{msg['sender']}|{msg['time']}|{msg['content'][:100]}"
    return hashlib.md5(unique_str.encode("utf-8")).hexdigest()


def truncate_message(text: str, max_len: int = 30) -> str:
    """Truncate message content for log output (privacy)."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
