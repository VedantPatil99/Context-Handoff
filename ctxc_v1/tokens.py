"""Token counting.

Uses tiktoken (cl100k_base) when it is installed, otherwise falls back to a
deterministic approximation: one token per word piece / punctuation mark,
with long words split into ~4-character chunks.  The approximation tracks
BPE counts on English prose to within roughly 10-15%, which is enough for
the *relative* comparisons the evaluation makes (all artifacts are counted
with the same counter).
"""
from __future__ import annotations

import re

_PIECE = re.compile(r"\w+|[^\w\s]")

try:  # pragma: no cover - depends on environment
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    BACKEND = "tiktoken/cl100k_base"

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

except Exception:  # ImportError or offline encoding download failure
    _ENC = None
    BACKEND = "approx (word-piece heuristic)"

    def count_tokens(text: str) -> int:
        n = 0
        for piece in _PIECE.findall(text):
            n += 1 if len(piece) <= 6 else (len(piece) + 3) // 4
        return n
