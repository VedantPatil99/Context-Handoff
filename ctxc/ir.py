"""Context IR Serializer.

Two artifacts are produced, mirroring an object file:

* the **header** (``.ctxir``) - the compact handoff artifact a new session
  reads first: active scope chain, constant pool, live versioned symbols.
  This is the only thing whose token cost is paid up front.
* the **bundle** (``.ctxir.json``) - header data plus the full symbol table
  (every version and use site), the audit log (every dropped symbol and every
  discarded lexeme with its rule) and the backing store of original lexeme
  text keyed by source location.  The loader resolves symbols against it on
  demand, the way a linker resolves a symbol against an archive only when the
  symbol is actually referenced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .constpool import Partition
from .extractor import FrontEndResult
from .reachability import Reachability
from .tokens import BACKEND

FORMAT = "ctxir/1"
_KIND = {"decision": "dec", "constraint": "con", "choice": "cho", "fact": "fact"}


def build_bundle(fe: FrontEndResult, reach: Reachability, part: Partition, *,
                 source: str, raw_text: str, n_turns: int) -> dict:
    scopes, symtab = fe.scopes, fe.symtab
    active = set(reach.active_chain)
    discard_counts: dict[str, int] = {}
    for d in fe.discards:
        discard_counts[d.rule] = discard_counts.get(d.rule, 0) + 1

    variables = []
    for name in part.variables:
        sym = symtab.symbols[name]
        cur = sym.current
        variables.append({"name": name, "ssa": cur.ssa, "value": cur.value, "kind": cur.kind,
                          "scope": cur.scope, "loc": cur.loc, "prior": len(sym.versions) - 1,
                          "pinned": sym.pinned})

    symbols = {}
    for sym in symtab:
        lr = reach.live.get(sym.name)
        symbols[sym.name] = {
            "kind": sym.kind,
            "aliases": sorted(sym.aliases),
            "pinned": sym.pinned,
            "retracted_at": sym.retracted_at,
            "retract_phrase": sym.retract_phrase,
            "live": lr is not None,
            "live_rule": lr.rule if lr else None,
            "live_why": lr.justification if lr else None,
            "versions": [{"n": v.n, "value": v.value, "kind": v.kind, "scope": v.scope,
                          "loc": v.loc, "killed_by": v.killed_by} for v in sym.versions],
            "uses": [asdict(u) for u in sym.uses],
        }

    return {
        "format": FORMAT,
        "meta": {
            "source": source,
            "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
            "turns": n_turns,
            "lexemes": len(fe.lexemes),
            "tokenizer": BACKEND,
            "restatements_folded": part.restatements_folded,
        },
        "scopes": [{"id": s.id, "name": s.name, "parent": s.parent, "depth": s.depth,
                    "opened_at": s.opened_at, "closed_at": s.closed_at, "active": s.id in active,
                    "abandoned": s.abandoned}
                   for s in scopes.scopes.values()],
        "scope_events": scopes.events,
        "active_chain": reach.active_chain,
        "constants": [asdict(c) for c in part.constants],
        "variables": variables,
        "symbols": symbols,
        "audit": {
            "dropped": [asdict(d) for d in reach.dropped],
            "drop_counts": reach.counts(),
            "discarded": [asdict(d) for d in fe.discards],
            "discard_counts": discard_counts,
            "events": [asdict(e) for e in fe.events],
        },
        "store": {lx.loc: {"role": lx.role, "kind": lx.kind, "text": lx.text} for lx in fe.lexemes},
    }


def _short(v: str) -> bool:
    return len(v.split()) <= 6


def render_header(b: dict) -> str:
    """Render the compact, LLM-readable handoff header (format v2).

    Compared with v1 it drops per-line kind/scope tags and constant ids, prints
    sentence-valued facts as bullets without their machine names, shows a
    versioned symbol's previous value inline, and lists cancelled/abandoned
    topics on one line so status questions are answerable without the loader.
    """
    m = b["meta"]
    counts = b["audit"]["drop_counts"]
    syms = b["symbols"]
    n_live = sum(1 for s in syms.values() if s["live"])
    chain = [s for s in b["scopes"] if s["active"] and s["parent"] is not None]
    lines = [f"CTXIR/2 {Path(m['source']).name} | {m['turns']} turns | {n_live} live, {sum(counts.values())} dropped "
             f"| resolve(name) gives original wording and history"]
    if chain:
        lines.append("NOW: " + " > ".join(s["name"] for s in chain))
    if b["constants"]:
        lines.append("CONST")
        for c in b["constants"]:
            v = c["value"]
            lines.append(f"{c['symbols'][0]} = {v}" if _short(v) else f"- {v}")
    if b["variables"]:
        lines.append("VAR")
        for v in b["variables"]:
            prev = syms[v["name"]]["versions"][-2]["value"]
            lines.append(f"{v['name']} = {v['value']} (was {prev})")
    gone = []
    for name, s in syms.items():
        if s["retracted_at"]:
            gone.append(f"{s.get('retract_phrase') or name.replace('_', ' ')} (cancelled)")
        elif not s["live"]:
            gone.append(f"{name.replace('_', ' ')} (abandoned)")
    if gone:
        lines.append("DROPPED: " + ", ".join(gone))
    return "\n".join(lines) + "\n"


def render_header_v1(b: dict) -> str:
    """The original v1 header format (kept for comparison)."""
    m = b["meta"]
    counts = b["audit"]["drop_counts"]
    n_live = sum(1 for s in b["symbols"].values() if s["live"])
    drop_s = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"
    lines = [
        f"CTXIR/1 src={Path(m['source']).name} turns={m['turns']} | live={n_live} "
        f"const={len(b['constants'])} var={len(b['variables'])} | dropped={sum(counts.values())} ({drop_s})",
        "Compiled conversation state. Values are current; ask to resolve(<name>) for original wording/history.",
    ]
    owners = {c["scope"] for c in b["constants"]} | {v["scope"] for v in b["variables"]}
    shown = [s for s in b["scopes"] if s["active"] or s["id"] in owners]
    hidden = len(b["scopes"]) - len(shown)
    lines.append("SCOPES (*active, x closed)" + (f" +{hidden} closed w/o live facts" if hidden else ""))
    for s in shown:
        mark = "*" if s["active"] else "x"
        cur = b["active_chain"][0] if b["active_chain"] else None
        tail = "  <- current" if s["id"] == cur else ""
        lines.append(f"{'  ' * s['depth']}{mark}{s['id']} {s['name']}{tail}")
    if b["constants"]:
        lines.append("CONST")
        for c in b["constants"]:
            pin = " pin" if c["pinned"] else ""
            lines.append(f"{c['id']} {', '.join(c['symbols'])} = {c['value']} [{_KIND[c['kind']]} {c['scope']}{pin}]")
    if b["variables"]:
        lines.append("VAR")
        for v in b["variables"]:
            pin = " pin" if v["pinned"] else ""
            lines.append(f"{v['ssa']} = {v['value']} [{_KIND[v['kind']]} {v['scope']}, {v['prior']} prior{pin}]")
    return "\n".join(lines) + "\n"


def write(bundle: dict, out_base: str | Path) -> tuple[Path, Path]:
    out_base = Path(out_base)
    header_p = out_base.with_suffix(".ctxir")
    bundle_p = out_base.with_suffix(".ctxir.json")
    header_p.write_text(render_header(bundle), encoding="utf-8")
    bundle_p.write_text(json.dumps(bundle, indent=1, ensure_ascii=False), encoding="utf-8")
    return header_p, bundle_p


def read_bundle(path: str | Path) -> dict:
    path = Path(path)
    if path.suffix == ".ctxir":
        path = path.with_suffix(".ctxir.json")
    b = json.loads(path.read_text(encoding="utf-8"))
    if b.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} bundle")
    return b
