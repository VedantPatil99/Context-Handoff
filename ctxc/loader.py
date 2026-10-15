"""Loader / New Session.

The receiving session pays only for the compact header up front.  Like a
linker that resolves an undefined symbol against an archive member only when
something references it, the loader scans each new query for references to
symbols and expands just those:

* a reference to a **live** symbol expands to its original defining
  sentence(s), the turn context around them, and its SSA version history;
* a reference to a **dropped** symbol is still resolvable from the backing
  store - it is relinked, and the expansion states which elimination rule
  had removed it, so nothing is ever silently unrecoverable;
* symbols that are never referenced are never expanded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import read_bundle, render_header
from .symtab import content_words, norm_value
from .tokens import count_tokens


@dataclass
class Resolution:
    symbol: str
    status: str            # live | dropped
    matched_by: str        # name | alias | value | ssa | keyword | explicit
    text: str
    tokens: int


@dataclass
class LoadStats:
    header_tokens: int = 0
    expanded_tokens: int = 0
    resolved: list[str] = field(default_factory=list)


_VAGUE = {"what", "when", "which", "where", "have", "need", "were", "with", "that", "this", "there", "they", "them",
          "into", "your", "much", "many", "still", "plan", "part", "make", "made", "going", "doing", "done"}


_HISTORY = re.compile(r"\b(?:why|before|previous(?:ly)?|originally|history|changed?|used to|what did (?:we|i) say|exact(?:ly)?|wording)\b", re.I)


def stem(w: str) -> str:
    """Tiny suffix stripper so "responses" matches "response" and "judges" matches "judge"."""
    for suf, rep in (("ies", "y"), ("ing", ""), ("es", ""), ("ed", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: len(w) - len(suf)] + rep
    return w


def _phrase(s: str) -> str:
    return " " + s.replace("_", " ") + " "


class ContextLoader:
    def __init__(self, bundle: dict):
        self.b = bundle
        self.header = render_header(bundle)
        self.stats = LoadStats(header_tokens=count_tokens(self.header))
        self._resolved: set[str] = set()
        self._drops = {}
        for d in bundle["audit"]["dropped"]:
            self._drops.setdefault(d["symbol"], []).append(d)
        # Distinctive words: stemmed content words (>= 4 chars) of a symbol's name/values
        # that occur in at most one other symbol - an "export list" for matching
        # queries that mention a fact without using its exact name or value.
        bag: dict[str, set[str]] = {}
        for name, sym in bundle["symbols"].items():
            ws = set(content_words(name.replace("_", " ")))
            for v in sym["versions"]:
                ws |= set(content_words(v["value"]))
            bag[name] = {stem(w) for w in ws if len(w) >= 4 and w not in _VAGUE}
        df: dict[str, int] = {}
        for ws in bag.values():
            for w in ws:
                df[w] = df.get(w, 0) + 1
        self._distinctive = {n: {w for w in ws if df[w] <= 2} for n, ws in bag.items()}

    @classmethod
    def from_file(cls, path) -> "ContextLoader":
        return cls(read_bundle(path))

    # -- reference detection ------------------------------------------
    def references(self, query: str) -> list[tuple[str, str]]:
        qn = " " + norm_value(query) + " "
        qwords = {stem(w) for w in content_words(query)}
        found: list[tuple[str, str]] = []
        for name, sym in self.b["symbols"].items():
            how = None
            if re.search(rf"\b{re.escape(name)}(?:\.\d+)?\b", query):
                how = "ssa" if re.search(rf"\b{re.escape(name)}\.\d+\b", query) else "name"
            elif len(name) >= 4 and _phrase(name) in qn:
                how = "name"
            elif any(len(a) >= 4 and _phrase(a) in qn for a in sym["aliases"]):
                how = "alias"
            else:
                for v in reversed(sym["versions"]):
                    vn = norm_value(v["value"])
                    if len(vn) >= 4 and len(vn.split()) <= 8 and f" {vn} " in qn:
                        how = "value"
                        break
                else:
                    if self._distinctive.get(name, set()) & qwords:
                        how = "keyword"
            if how:
                found.append((name, how))
        return found

    # -- expansion ------------------------------------------------------
    def _context(self, loc: str, radius: int = 1) -> list[str]:
        """Original lexeme at ``loc`` plus neighbours in the same turn."""
        turn, seq = loc[1:].split(".")
        lines = []
        for d in range(-radius, radius + 1):
            key = f"t{turn}.{int(seq) + d}"
            lx = self.b["store"].get(key)
            if lx and lx["kind"] != "CODE":
                mark = ">" if d == 0 else " "
                lines.append(f"  {mark} [{key} {lx['role']}] {lx['text']}")
        return lines

    def resolve(self, name: str, matched_by: str = "explicit") -> Resolution:
        sym = self.b["symbols"][name]
        status = "live" if sym["live"] else "dropped"
        out = [f"resolve({name}) -> {status}" + (f" ({sym['live_rule']}: {sym['live_why']})" if sym["live"] else "")]
        for v in sym["versions"]:
            killed = f"  killed_by={v['killed_by']}" if v["killed_by"] else ""
            out.append(f" {name}.{v['n']} = {v['value']}  [{v['kind']} @{v['scope']} {v['loc']}]{killed}")
            out.extend(self._context(v["loc"]))
        if not sym["live"]:
            for d in self._drops.get(name, []):
                if d["rule"] != "R-SSA":
                    out.append(f" NOTE: eliminated by {d['rule']}: {d['justification']}")
        text = "\n".join(out)
        res = Resolution(name, status, matched_by, text, count_tokens(text))
        if name not in self._resolved:
            self._resolved.add(name)
            self.stats.resolved.append(name)
            self.stats.expanded_tokens += res.tokens
        return res

    def on_query(self, query: str) -> list[Resolution]:
        """Resolve every symbol the query references (each at most once per session)."""
        out = []
        history = bool(_HISTORY.search(query))
        for name, how in self.references(query):
            if name in self._resolved:
                continue
            # A live symbol's current value is already in the header (already linked); fetch its
            # original wording only when the question asks about history or names it explicitly.
            if self.b["symbols"][name]["live"] and not history and how not in ("ssa", "name"):
                continue
            out.append(self.resolve(name, how))
        return out

    def build_prompt(self, query: str) -> str:
        """The context a new session would actually receive for ``query``."""
        res = self.on_query(query)
        parts = [self.header]
        if res:
            parts.append("RESOLVED SYMBOLS")
            parts.extend(r.text for r in res)
        parts.append(f"USER: {query}")
        return "\n".join(parts)
