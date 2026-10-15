"""Symbol Table Builder with SSA-style versioning.

Each durable fact is a *symbol*.  Every time its value changes a new
*version* is appended (``title.1``, ``title.2`` ...) instead of overwriting -
the Static Single Assignment discipline: each version is assigned exactly
once, and every later reference names exactly one definition.  The previous
version is marked as killed by the new one, which is what later lets the
reachability pass drop it as a dead store while still being able to say
precisely *why* it was dropped.

Name resolution follows lexical scoping: an unqualified key such as
"title" is resolved by searching for compatible symbols defined in the
innermost active scope first, then each enclosing scope, then anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_DETERMINERS = {
    "the", "a", "an", "our", "my", "your", "their", "its", "this", "that",
    "these", "those", "his", "her", "we", "us", "i", "new", "current", "final",
}
_STOP = _DETERMINERS | {
    "of", "for", "to", "in", "on", "and", "or", "with", "is", "be", "it", "as",
    "at", "by", "from", "will", "should", "would", "can", "all", "any", "just",
    "so", "then", "also", "not", "no", "do", "dont", "please", "let", "lets",
}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in _STOP and len(w) > 1]


def slug(key: str, max_words: int = 4) -> str:
    ws = words(key)
    while ws and ws[0] in _DETERMINERS:
        ws.pop(0)
    ws = [w for w in ws if w not in ("s",)]
    return "_".join(ws[:max_words]) or "fact"


def norm_value(v: str) -> str:
    return " ".join(words(v))


def similar(a: str, b: str, threshold: float = 0.8) -> bool:
    na, nb = norm_value(a), norm_value(b)
    if na == nb:
        return True
    wa, wb = set(content_words(a)), set(content_words(b))
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


@dataclass
class Version:
    symbol: str
    n: int
    value: str
    kind: str          # fact | decision | constraint | choice
    scope: str         # scope id where this version was defined
    loc: str           # lexeme location of the defining sentence
    killed_by: str | None = None   # "name.k" (superseded) or "retract@loc"

    @property
    def ssa(self) -> str:
        return f"{self.symbol}.{self.n}"


@dataclass
class Use:
    loc: str
    scope: str
    how: str  # restate | name | value | explicit


@dataclass
class Symbol:
    name: str
    kind: str
    aliases: set[str] = field(default_factory=set)
    versions: list[Version] = field(default_factory=list)
    uses: list[Use] = field(default_factory=list)
    pinned: bool = False
    retracted_at: str | None = None
    retract_phrase: str | None = None   # the user's words when cancelling it ("the SHAP plot")

    @property
    def current(self) -> Version:
        return self.versions[-1]

    @property
    def decl_scope(self) -> str:
        """Scope of the first definition.  Later assignments from inner scopes
        update the symbol but do not move it (an assignment to an outer
        variable from an inner block does not change the variable's scope)."""
        return self.versions[0].scope

    @property
    def retracted(self) -> bool:
        return self.retracted_at is not None


class SymbolTable:
    def __init__(self):
        self.symbols: dict[str, Symbol] = {}
        self.order: list[str] = []   # definition order (for recency)
        self._touch: dict[str, int] = {}
        self._clock = 0

    def __iter__(self):
        return (self.symbols[n] for n in self.order)

    def __len__(self):
        return len(self.symbols)

    def _bump(self, name: str):
        self._clock += 1
        self._touch[name] = self._clock

    # -- name resolution ---------------------------------------------
    def resolve(self, key: str, chain: list[str]) -> Symbol | None:
        s = slug(key)
        if s in self.symbols:
            return self.symbols[s]
        want = set(s.split("_"))
        head = s.split("_")[-1]
        cands = []
        for sym in self.symbols.values():
            if s in sym.aliases:
                return sym
            have = set(sym.name.split("_"))
            if sym.name.split("_")[-1] != head and head not in have:
                continue
            if want <= have or have <= want:
                cands.append(sym)
        if not cands:
            return None

        def rank(sym: Symbol):
            sc = sym.current.scope
            depth = chain.index(sc) if sc in chain else len(chain)
            return (depth, -self._touch.get(sym.name, 0))

        return min(cands, key=rank)

    def find_by_value(self, text: str) -> Symbol | None:
        nt = norm_value(text)
        if len(nt) < 2:
            return None
        best = None
        for sym in self.symbols.values():
            if sym.retracted:
                continue
            nv = norm_value(sym.current.value)
            if nt == nv or re.search(rf"\b{re.escape(nt)}\b", nv):
                if best is None or self._touch.get(sym.name, 0) > self._touch.get(best.name, 0):
                    best = sym
        return best

    def _fresh_name(self, base: str) -> str:
        if base not in self.symbols:
            return base
        i = 2
        while f"{base}_{i}" in self.symbols:
            i += 1
        return f"{base}_{i}"

    # -- mutation -----------------------------------------------------
    def assign(self, key: str, value: str, kind: str, scope: str, loc: str,
               chain: list[str], *, new_symbol: bool = False,
               target: Symbol | None = None) -> tuple[str, Symbol, Version | None]:
        """Record an assignment ``key := value``.

        Returns ``(event, symbol, version)`` where event is one of
        ``define`` (new symbol), ``revise`` (new SSA version) or
        ``restate`` (same value repeated - recorded as a use, no version).
        """
        sym = target if target is not None else (None if new_symbol else self.resolve(key, chain))
        if sym is not None and sym.retracted:
            # Re-definition after a retraction revives the symbol.
            sym.retracted_at = None
            if (sym.current.killed_by or "").startswith("retract@"):
                sym.current.killed_by = None
        if sym is None:
            name = self._fresh_name(slug(key))
            sym = Symbol(name, kind)
            sym.aliases.add(slug(key))
            self.symbols[name] = sym
            self.order.append(name)
            v = Version(name, 1, value, kind, scope, loc)
            sym.versions.append(v)
            self._bump(name)
            return "define", sym, v
        sym.aliases.add(slug(key))
        if similar(sym.current.value, value):
            sym.uses.append(Use(loc, scope, "restate"))
            self._bump(sym.name)
            return "restate", sym, None
        v = Version(sym.name, len(sym.versions) + 1, value, kind, scope, loc)
        sym.current.killed_by = v.ssa
        sym.versions.append(v)
        self._bump(sym.name)
        return "revise", sym, v

    def retract(self, sym: Symbol, loc: str):
        sym.retracted_at = loc
        if sym.current.killed_by is None:
            sym.current.killed_by = f"retract@{loc}"
        self._bump(sym.name)

    def use(self, sym: Symbol, loc: str, scope: str, how: str):
        if sym.uses and sym.uses[-1].loc == loc:
            return
        sym.uses.append(Use(loc, scope, how))
