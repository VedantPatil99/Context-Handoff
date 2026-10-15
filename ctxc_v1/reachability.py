"""Reachability Analysis (Dead-Symbol Elimination).

The analogue of dead-code / dead-store elimination.  It is a classic
mark-and-sweep over a graph whose nodes are symbol versions:

Root set (marked live immediately):
  L-PIN    the user explicitly pinned the fact ("remember that ...")
  L-SCOPE  the current version was defined in a scope on the *active scope
           chain* (the innermost open task and every enclosing task, up to the
           root - exactly the scopes whose bindings are still visible)
  L-USE    the current version is *used* (named, or its value restated)
           from a scope on the active chain after it was defined - the fact
           escaped its declaring scope

Propagation (worklist):
  L-REF    the current value of a live symbol mentions this symbol (by name
           or by its value), so it is transitively reachable

Sweep - everything unmarked is dropped, each with an explicit rule:
  R-SSA    an earlier version killed by a later assignment (dead store)
  R-KILL   the symbol was explicitly retracted by the user
  R-SCOPE  defined in a scope that is no longer on the active chain and never
           used from it

Because every drop is a record (rule + justification + source location),
the handoff can enumerate exactly what it discarded and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .extractor import FrontEndResult
from .symtab import Symbol, norm_value


def loc_key(loc: str) -> tuple[int, int]:
    t, s = loc[1:].split(".")
    return int(t), int(s)


@dataclass
class LiveRecord:
    symbol: str
    ssa: str
    rule: str
    justification: str


@dataclass
class DropRecord:
    symbol: str
    ssa: str
    value: str
    kind: str
    scope: str
    loc: str
    rule: str
    justification: str


@dataclass
class Reachability:
    live: dict[str, LiveRecord] = field(default_factory=dict)
    dropped: list[DropRecord] = field(default_factory=list)
    active_chain: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.dropped:
            out[d.rule] = out.get(d.rule, 0) + 1
        return out


def _mentions(text: str, sym: Symbol) -> bool:
    tn = f" {norm_value(text)} "
    phrase = f" {sym.name.replace('_', ' ')} "
    if len(sym.name) >= 4 and phrase in tn:
        return True
    vn = norm_value(sym.current.value)
    return len(vn) >= 5 and len(vn.split()) <= 8 and f" {vn} " in tn


def analyze(fe: FrontEndResult, global_kinds: frozenset[str] = frozenset()) -> Reachability:
    """Mark-and-sweep over symbols.

    ``global_kinds`` is an *ablation*, not part of the proposal's design: symbol
    kinds listed there (e.g. ``{"constraint"}``) are treated as declared in the
    root scope - like a global/extern declaration - so closing the phase they
    were stated in does not kill them.  The default (empty) is the strict rule
    described in the proposal.
    """
    scopes, symtab = fe.scopes, fe.symtab
    chain = scopes.chain()           # innermost first
    active = set(chain)
    r = Reachability(active_chain=chain)

    def sname(sid: str) -> str:
        return f"{sid} '{scopes.scopes[sid].name}'"

    # --- mark: root set ------------------------------------------------
    worklist: list[Symbol] = []
    for sym in symtab:
        if sym.retracted:
            continue
        cur = sym.current
        if sym.pinned:
            r.live[sym.name] = LiveRecord(sym.name, cur.ssa, "L-PIN", "pinned by user")
        elif sym.kind in global_kinds:
            r.live[sym.name] = LiveRecord(sym.name, cur.ssa, "L-GLOBAL",
                                          f"kind '{sym.kind}' treated as global (ablation)")
        elif sym.decl_scope in active or cur.scope in active:
            sid = sym.decl_scope if sym.decl_scope in active else cur.scope
            r.live[sym.name] = LiveRecord(sym.name, cur.ssa, "L-SCOPE",
                                          f"declared/assigned in active scope {sname(sid)}")
        else:
            uses = [u for u in sym.uses if u.scope in active and loc_key(u.loc) > loc_key(cur.loc)]
            if uses:
                u = uses[-1]
                r.live[sym.name] = LiveRecord(sym.name, cur.ssa, "L-USE",
                                              f"{u.how} use at {u.loc} in active scope {sname(u.scope)}")
        if sym.name in r.live:
            worklist.append(sym)

    # --- mark: propagate through value references -----------------------
    candidates = [s for s in symtab if not s.retracted]
    while worklist:
        src = worklist.pop()
        for sym in candidates:
            if sym.name in r.live or sym is src:
                continue
            if _mentions(src.current.value, sym):
                r.live[sym.name] = LiveRecord(sym.name, sym.current.ssa, "L-REF",
                                              f"referenced by live symbol {src.current.ssa}")
                worklist.append(sym)

    # --- sweep ------------------------------------------------------------
    for sym in symtab:
        for v in sym.versions[:-1]:
            r.dropped.append(DropRecord(sym.name, v.ssa, v.value, v.kind, v.scope, v.loc, "R-SSA",
                                        f"dead store: superseded by {v.killed_by}"))
        cur = sym.current
        if sym.retracted:
            r.dropped.append(DropRecord(sym.name, cur.ssa, cur.value, cur.kind, cur.scope, cur.loc, "R-KILL",
                                        f"explicitly retracted at {sym.retracted_at}"))
        elif sym.name not in r.live:
            sc = scopes.scopes[sym.decl_scope]
            why = f"declared in {sname(sym.decl_scope)}"
            why += f", closed at {sc.closed_at}" if sc.closed else ", not on active chain"
            later = [u for u in sym.uses if loc_key(u.loc) > loc_key(cur.loc)]
            if later:
                why += f"; last use at {later[-1].loc} in inactive {sname(later[-1].scope)}"
            else:
                why += "; never used afterwards"
            why += f"; active chain = [{', '.join(chain)}]"
            r.dropped.append(DropRecord(sym.name, cur.ssa, cur.value, cur.kind, cur.scope, cur.loc, "R-SCOPE", why))
    return r
