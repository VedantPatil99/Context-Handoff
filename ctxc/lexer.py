"""Lexical analysis: segment turns into sentence-level lexemes.

A conversation's "tokens" for our purposes are sentences (and a few
structural lexemes such as headings, list items and fenced code blocks).
Each lexeme carries its source position ``(turn, seq)`` so every later stage
can point back at exact original text - the basis of auditability and of
on-demand symbol resolution in the loader.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .transcript import Turn


@dataclass
class Lexeme:
    turn: int
    seq: int          # position within the turn
    role: str
    kind: str         # SENTENCE | HEADING | ITEM | CODE
    text: str
    level: int = 0    # heading level for HEADING

    @property
    def loc(self) -> str:
        return f"t{self.turn}.{self.seq}"


_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.+)$")
# Split on sentence-final punctuation followed by whitespace and a letter /
# quote / digit, but not after common abbreviations.  Lowercase starts are
# accepted because chat users often do not capitalise ("good point. change ...").
_ABBREV = r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bvs\.)(?<!\bMr\.)(?<!\bDr\.)(?<!\bMs\.)(?<!\bSt\.)"
_SENT_SPLIT = re.compile(_ABBREV + r"(?<=[.!?])\s+(?=[\"'(\[A-Za-z0-9*])")


def split_sentences(text: str) -> list[str]:
    out = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if part:
            out.append(part)
    return out


def lex_turn(turn: Turn) -> list[Lexeme]:
    lexemes: list[Lexeme] = []
    in_code, code_buf = False, []
    para: list[str] = []

    def emit(kind: str, text: str, level: int = 0):
        lexemes.append(Lexeme(turn.index, len(lexemes), turn.role, kind, text, level))

    def flush_para():
        if para:
            for s in split_sentences(" ".join(para)):
                emit("SENTENCE", s)
            para.clear()

    for line in turn.text.splitlines():
        if _FENCE.match(line):
            if in_code:
                code_buf.append(line)
                emit("CODE", "\n".join(code_buf))
                code_buf, in_code = [], False
            else:
                flush_para()
                in_code, code_buf = True, [line]
            continue
        if in_code:
            code_buf.append(line)
            continue
        if not line.strip():
            flush_para()
            continue
        h = _HEADING.match(line)
        if h:
            flush_para()
            emit("HEADING", h.group(2).strip("*_ "), len(h.group(1)))
            continue
        it = _ITEM.match(line)
        if it:
            flush_para()
            for s in split_sentences(it.group(1)):
                emit("ITEM", s)
            continue
        para.append(line.strip())
    if in_code:
        emit("CODE", "\n".join(code_buf))
    flush_para()
    return lexemes


def lex(turns: list[Turn]) -> list[Lexeme]:
    out: list[Lexeme] = []
    for t in turns:
        out.extend(lex_turn(t))
    return out
