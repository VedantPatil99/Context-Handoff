"""Transcript loading: the "source file" reader of the context compiler.

Every supported input format is normalised into a flat list of ``Turn``
objects.  Supported formats:

* plain text / markdown with speaker prefixes (``User:``, ``Assistant:``,
  ``Human:``, ``AI:`` and common assistant names) at the start of a line
* JSON list of ``{"role": ..., "content": ...}`` messages, or an object with
  a ``"messages"`` key holding such a list
* chat-app data export with ``chat_messages`` (``sender`` / ``text``)
* ChatGPT data export (``mapping`` tree of message nodes)
* JSON Lines, one message per line (including coding-agent session logs)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Turn:
    index: int
    role: str  # "user" | "assistant" | "system"
    text: str


_ROLE_ALIASES = {
    "user": "user", "human": "user", "you": "user", "me": "user",
    "assistant": "assistant", "claude": "assistant", "chatgpt": "assistant",
    "ai": "assistant", "bot": "assistant", "model": "assistant", "gpt": "assistant",
    "system": "system",
}

_SPEAKER = re.compile(
    r"^\s*(?:\*\*)?(user|human|you|assistant|claude|chatgpt|ai|bot|model|gpt|system)(?:\*\*)?\s*:\s?(.*)$",
    re.IGNORECASE,
)


def _norm_role(role: str) -> str:
    return _ROLE_ALIASES.get(str(role).strip().lower(), "assistant")


def _content_text(content) -> str:
    """Flatten the many shapes message content can take into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in (None, "text") and "text" in block:
                    parts.append(str(block["text"]))
                elif "parts" in block:
                    parts.append(_content_text(block["parts"]))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if "parts" in content:
            return _content_text(content["parts"])
        if "text" in content:
            return str(content["text"])
    return str(content)


def parse_text(raw: str) -> list[Turn]:
    turns: list[Turn] = []
    role, buf = None, []
    for line in raw.splitlines():
        m = _SPEAKER.match(line)
        if m:
            if role is not None:
                turns.append(Turn(len(turns), role, "\n".join(buf).strip()))
            role, buf = _norm_role(m.group(1)), [m.group(2)]
        else:
            if role is None:
                if not line.strip():
                    continue
                role = "user"  # text before any speaker tag
            buf.append(line)
    if role is not None:
        turns.append(Turn(len(turns), role, "\n".join(buf).strip()))
    return [t for t in turns if t.text]


def _from_messages(msgs) -> list[Turn]:
    turns = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        author = m.get("author")
        if isinstance(author, dict):
            author = author.get("role")
        role = m.get("role") or m.get("sender") or m.get("type") or author
        if role not in _ROLE_ALIASES and isinstance(m.get("message"), dict):
            role = None
        content = m.get("content", m.get("text"))
        if isinstance(m.get("message"), dict):  # wrapped message objects
            inner = m["message"]
            role = inner.get("role", role)
            content = inner.get("content", content)
        if role is None and content is None:
            continue
        text = _content_text(content).strip()
        if text:
            turns.append(Turn(len(turns), _norm_role(role or "assistant"), text))
    return turns


def _from_chatgpt_mapping(conv: dict) -> list[Turn]:
    mapping = conv["mapping"]
    # Follow the chain from current_node back to the root, then reverse.
    node_id = conv.get("current_node")
    if node_id is None:  # fall back to the last leaf
        node_id = next(k for k, v in mapping.items() if not v.get("children"))
    chain = []
    while node_id:
        node = mapping[node_id]
        if node.get("message"):
            chain.append(node["message"])
        node_id = node.get("parent")
    msgs = []
    for msg in reversed(chain):
        role = msg.get("author", {}).get("role", "assistant")
        if role == "tool":
            continue
        msgs.append({"role": role, "content": msg.get("content")})
    return _from_messages(msgs)


def parse_json(data) -> list[Turn]:
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and ("chat_messages" in data[0] or "mapping" in data[0]):
            data = data[0]  # export file with many conversations: take the first
        else:
            return _from_messages(data)
    if isinstance(data, dict):
        if "chat_messages" in data:
            return _from_messages(data["chat_messages"])
        if "mapping" in data:
            return _from_chatgpt_mapping(data)
        if "messages" in data:
            return _from_messages(data["messages"])
    raise ValueError("unrecognised JSON transcript layout")


def parse_any(raw: str, name: str = "") -> list[Turn]:
    """Detect the format of ``raw`` (JSON, JSON Lines or speaker-prefixed text)."""
    stripped = raw.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            return parse_json(json.loads(raw))
        except json.JSONDecodeError:
            pass
        if name.endswith(".jsonl") or stripped.startswith("{"):
            try:
                return _from_messages([json.loads(l) for l in raw.splitlines() if l.strip()])
            except json.JSONDecodeError:
                pass
    return parse_text(raw)


def load_transcript(path: str | Path) -> list[Turn]:
    path = Path(path)
    return parse_any(path.read_text(encoding="utf-8"), path.name)


def render(turns: list[Turn]) -> str:
    """Render turns back to plain text (what a full-transcript handoff costs)."""
    return "\n\n".join(f"{t.role.capitalize()}: {t.text}" for t in turns)
