"""Constant Pool Separator.

A live symbol that was assigned exactly once and never revised is a
compile-time constant of the conversation.  Like a JVM class-file constant
pool, such facts are hoisted out of the scope-structured symbol section into
a flat pool, stored once regardless of how many times they were restated
(restatements were already folded into uses by the symbol table).  Identical
values shared by several constants are interned to a single pool entry.

Symbols with more than one version stay in the versioned symbol section,
since their history (``title.3, 2 prior``) is information a reader needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .reachability import Reachability
from .symtab import SymbolTable, norm_value


@dataclass
class Constant:
    id: str
    symbols: list[str]
    value: str
    kind: str
    scope: str
    loc: str
    restated: int = 0
    pinned: bool = False


@dataclass
class Partition:
    constants: list[Constant] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)   # live, multi-version symbol names
    restatements_folded: int = 0


def separate(symtab: SymbolTable, reach: Reachability) -> Partition:
    p = Partition()
    by_value: dict[str, Constant] = {}
    for sym in symtab:
        if sym.name not in reach.live:
            continue
        restates = sum(1 for u in sym.uses if u.how == "restate")
        p.restatements_folded += restates
        if len(sym.versions) == 1:
            key = norm_value(sym.current.value)
            if key in by_value:
                c = by_value[key]
                c.symbols.append(sym.name)
                c.restated += restates + 1
                c.pinned |= sym.pinned
                continue
            c = Constant(f"K{len(p.constants)}", [sym.name], sym.current.value, sym.current.kind,
                         sym.current.scope, sym.current.loc, restates, sym.pinned)
            by_value[key] = c
            p.constants.append(c)
        else:
            p.variables.append(sym.name)
    return p
