"""Command-line interface.

    python -m ctxc compile  <transcript> [-o OUT_BASE] [--global-constraints]
    python -m ctxc audit    <bundle.ctxir.json>
    python -m ctxc resolve  <bundle.ctxir.json> <symbol>
    python -m ctxc query    <bundle.ctxir.json> "<new message>"
    python -m ctxc session  <bundle.ctxir.json>          (interactive)
    python -m ctxc eval     <transcript> --summary S [--gold G] [--queries Q] [--probes P] [-o REPORT.md]
    python -m ctxc dashboard [-o results/dashboard.html] [--detailed]
    python -m ctxc serve    [--port 8000]                 (local web app backed by the pipeline)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ir
from .evaluate import evaluate
from .loader import ContextLoader
from .pipeline import compile_file
from .tokens import count_tokens


def _out(s: str):
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))


def cmd_compile(a):
    gk = frozenset({"constraint"}) if a.global_constraints else frozenset()
    r = compile_file(a.transcript, global_kinds=gk)
    base = Path(a.output) if a.output else Path(a.transcript).with_suffix("")
    hp, bp = ir.write(r.bundle, base)
    _out(r.header)
    _out(f"wrote {hp} ({count_tokens(r.header)} tokens) and {bp}")


def cmd_audit(a):
    b = ir.read_bundle(a.bundle)
    _out("ELIMINATED SYMBOLS")
    for d in b["audit"]["dropped"]:
        _out(f"  {d['ssa']:<28} {d['rule']:<8} {d['value'][:60]!r}\n      {d['justification']}")
    _out("\nLIVE SYMBOLS")
    for name, s in b["symbols"].items():
        if s["live"]:
            _out(f"  {name:<28} {s['live_rule']:<8} {s['live_why']}")
    _out("\nEXTRACTION DISCARDS: " + ", ".join(f"{k} {v}" for k, v in sorted(b["audit"]["discard_counts"].items())))
    if a.verbose:
        for d in b["audit"]["discarded"]:
            _out(f"  {d['loc']:<8} {d['rule']:<14} {b['store'][d['loc']]['text'][:100]}")


def cmd_resolve(a):
    loader = ContextLoader.from_file(a.bundle)
    if a.symbol not in loader.b["symbols"]:
        sys.exit(f"unknown symbol {a.symbol!r}; known: {', '.join(loader.b['symbols'])}")
    _out(loader.resolve(a.symbol).text)


def cmd_query(a):
    loader = ContextLoader.from_file(a.bundle)
    _out(loader.build_prompt(a.message))
    _out(f"\n[header {loader.stats.header_tokens} tokens + expanded {loader.stats.expanded_tokens} tokens; "
         f"resolved: {', '.join(loader.stats.resolved) or 'none'}]")


def cmd_session(a):
    loader = ContextLoader.from_file(a.bundle)
    _out(loader.header)
    _out(f"[{loader.stats.header_tokens} tokens loaded. Type a message; symbols it references are resolved. "
         "Ctrl-D / 'quit' to exit.]")
    while True:
        try:
            msg = input("you> ").strip()
        except EOFError:
            break
        if msg in ("quit", "exit"):
            break
        res = loader.on_query(msg)
        for r in res:
            _out(r.text)
        _out(f"[+{sum(r.tokens for r in res)} tokens; session total "
             f"{loader.stats.header_tokens + loader.stats.expanded_tokens}]")


def cmd_eval(a):
    queries = None
    if a.queries:
        queries = [l.strip() for l in Path(a.queries).read_text(encoding="utf-8").splitlines() if l.strip()]
    md, data = evaluate(a.transcript, a.summary, a.gold, queries, a.probes)
    out = Path(a.output) if a.output else Path(a.transcript).with_suffix(".eval.md")
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    _out(md)
    _out(f"wrote {out}")


def cmd_dashboard(a):
    from .dashboard import ROOT, build
    ROOT_RESULTS = ROOT / "results"
    if a.output:
        out = build(a.output, detailed=a.detailed)
    else:
        out = build(detailed=True, out=ROOT_RESULTS / "dashboard_detailed.html") if a.detailed else build()
    _out(f"wrote {out} - open it in a browser")


def cmd_serve(a):
    from .server import serve
    serve(a.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="ctxc", description="Context compiler: conversation -> auditable Context IR")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("compile"); c.add_argument("transcript"); c.add_argument("-o", "--output")
    c.add_argument("--global-constraints", action="store_true", help="ablation: treat constraints as global")
    c.set_defaults(fn=cmd_compile)
    c = sp.add_parser("audit"); c.add_argument("bundle"); c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(fn=cmd_audit)
    c = sp.add_parser("resolve"); c.add_argument("bundle"); c.add_argument("symbol"); c.set_defaults(fn=cmd_resolve)
    c = sp.add_parser("query"); c.add_argument("bundle"); c.add_argument("message"); c.set_defaults(fn=cmd_query)
    c = sp.add_parser("session"); c.add_argument("bundle"); c.set_defaults(fn=cmd_session)
    c = sp.add_parser("eval"); c.add_argument("transcript"); c.add_argument("--summary", required=True)
    c.add_argument("--gold"); c.add_argument("--queries"); c.add_argument("--probes", help="follow-up questions JSON"); c.add_argument("-o", "--output")
    c.set_defaults(fn=cmd_eval)
    c = sp.add_parser("dashboard", help="write a self-contained HTML dashboard for the samples")
    c.add_argument("-o", "--output"); c.add_argument("--detailed", action="store_true", help="the detailed multi-tab view")
    c.set_defaults(fn=cmd_dashboard)
    c = sp.add_parser("serve", help="run the explorer as a local web app backed by the pipeline")
    c.add_argument("--port", type=int, default=8000); c.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
