"""Dashboard generator: compiles samples and writes a self-contained HTML page.

The page shows each compiler pass on real pipeline output (no numbers are
typed into the page by hand): a step-by-step replay of the front end, the
SSA symbol table, the scope tree, the reachability verdicts, the prose vs IR
comparison, and a loader playground.

    python -m ctxc dashboard [-o results/dashboard.html] [--detailed]
"""
from __future__ import annotations

import json
from pathlib import Path

from .evaluate import _has
from .loader import ContextLoader
from .pipeline import CompileResult, compile_turns
from .probe import load_probes, run_probes
from .reachability import loc_key
from .tokens import BACKEND, count_tokens
from .transcript import load_transcript, render

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("dashboard_template.html")
DETAILED_TEMPLATE = Path(__file__).with_name("dashboard_detailed_template.html")

SAMPLES = [
    {"id": "campus_events", "title": "Campus events app (69 turns)",
     "note": "Development sample: extractor bugs were fixed while looking at this transcript."},
    {"id": "heldout_ml_report", "title": "Held-out: ML report (39 turns)",
     "note": "Held-out sample, written after the fixes. First run carried 5/12 facts; "
             "this view is after the informal-text fix, so it is no longer a held-out measure."},
    {"id": "fresh_hackathon", "title": "Fresh held-out: hackathon (49 turns)",
     "note": "Fresh held-out sample, written and locked before any v2 code existed (samples/fresh_hackathon.LOCKED.sha256)."},
]


def _fate(res: CompileResult, needles: list[str]) -> dict:
    """Structured version of evaluate._ir_fate."""
    b, header = res.bundle, res.header
    if _has(header, needles):
        for name, sym in b["symbols"].items():
            if sym["live"] and _has(sym["versions"][-1]["value"] + " " + name, needles):
                return {"status": "carried", "symbol": name, "rule": sym["live_rule"]}
        return {"status": "carried", "symbol": None, "rule": None}
    for d in b["audit"]["dropped"]:
        if _has(d["value"] + " " + d["symbol"], needles):
            return {"status": "dropped", "symbol": d["ssa"], "rule": d["rule"]}
    for name, sym in b["symbols"].items():
        if any(_has(v["value"], needles) for v in sym["versions"]):
            return {"status": "extracted", "symbol": name, "rule": None}
    return {"status": "missed", "symbol": None, "rule": None}


def _config(res: CompileResult) -> dict:
    b = res.bundle
    loader = ContextLoader(b)
    resolve = {}
    for name in b["symbols"]:
        r = loader.resolve(name)
        resolve[name] = {"text": r.text, "tokens": r.tokens}
    return {
        "header": res.header,
        "header_tokens": count_tokens(res.header),
        "symbols": b["symbols"],
        "scopes": b["scopes"],
        "scope_events": b["scope_events"],
        "active_chain": b["active_chain"],
        "constants": b["constants"],
        "variables": b["variables"],
        "dropped": b["audit"]["dropped"],
        "drop_counts": b["audit"]["drop_counts"],
        "discard_counts": b["audit"]["discard_counts"],
        "resolve": resolve,
        "distinctive": {k: sorted(v) for k, v in loader._distinctive.items()},
    }


def _lexemes(res: CompileResult) -> list[dict]:
    """Every lexeme with its fate and the scope stack active while it was parsed."""
    b = res.bundle
    events: dict[str, list] = {}
    for e in b["audit"]["events"]:
        events.setdefault(e["loc"], []).append({"op": e["op"], "ssa": e["ssa"], "rule": e["rule"]})
    discards = {d["loc"]: d["rule"] for d in b["audit"]["discarded"]}
    sev = b["scope_events"]
    stack, j, out = ["s0"], 0, []
    for lx in res.front.lexemes:
        while j < len(sev) and loc_key(sev[j]["loc"]) <= loc_key(lx.loc):
            ev = sev[j]
            if ev["op"] in ("open", "reopen"):
                stack.append(ev["scope"])
            elif ev["op"] == "close" and ev["scope"] in stack:
                stack.remove(ev["scope"])
            j += 1
        out.append({"loc": lx.loc, "turn": lx.turn, "role": lx.role, "kind": lx.kind, "text": lx.text,
                    "stack": list(stack), "events": events.get(lx.loc, []), "discard": discards.get(lx.loc)})
    return out


def _live(res: CompileResult) -> dict[str, str]:
    return {n: s["versions"][-1]["value"] for n, s in res.bundle["symbols"].items() if s["live"]}


def llm_compare(turns, source: str, rules: CompileResult, summary: str | None, probes: list[dict] | None) -> dict:
    """Rules-only front end vs rules + grounded LLM pass, on the same chat."""
    from .llm_extract import MODEL
    try:
        post = compile_turns(turns, source, extractor="llm")
    except OSError as e:   # Ollama not running and no cached output for this chat
        return {"error": f"The local model ({MODEL}) is not reachable and there is no cached run for this chat: {e}"}
    pre_live, post_live = _live(rules), _live(post)
    pre_vals = {v.lower() for v in pre_live.values()}
    facts = []
    for name, val in post_live.items():
        if name not in pre_live:
            facts.append({"change": "added" if val.lower() not in pre_vals else "renamed", "symbol": name, "before": None, "after": val})
        elif pre_live[name] != val:
            facts.append({"change": "revised", "symbol": name, "before": pre_live[name], "after": val})
    for name, val in pre_live.items():
        if name not in post_live:
            facts.append({"change": "removed", "symbol": name, "before": val, "after": None})
    out = {
        "model": MODEL,
        "log": post.front.llm_log,
        "facts": facts,
        "live": {"before": len(pre_live), "after": len(post_live)},
        "tokens": {"before": count_tokens(rules.header), "after": count_tokens(post.header)},
        "header": post.header,
        "probes": None,
    }
    if probes:
        a, b = run_probes(rules.bundle, summary, probes), run_probes(post.bundle, summary, probes)
        out["probes"] = {"n": a["n"], "before": a["totals"]["ir"], "after": b["totals"]["ir"],
                         "summary": a["totals"]["summary"], "summary_tokens": a["tokens"]["summary"],
                         "tokens_before": a["tokens"]["ir_avg"], "tokens_after": b["tokens"]["ir_avg"],
                         "changed": [{"q": x["q"], "before": x["ir"], "after": y["ir"]}
                                     for x, y in zip(a["rows"], b["rows"]) if x["ir"] != y["ir"]],
                         "rows": [{"q": x["q"], "summary": x["summary"], "before": x["ir"], "after": y["ir"]}
                                  for x, y in zip(a["rows"], b["rows"])]}
    return out


def analyze_chat(turns, source: str, summary: str | None = None, gold: dict | None = None,
                 queries: list[str] | None = None, probes: list[dict] | None = None,
                 llm: bool = False, **meta) -> tuple[dict, CompileResult]:
    """Compile one chat both ways and package everything the pages display.

    ``summary`` and ``gold`` are optional (a pasted chat may have neither);
    the corresponding fields are then ``None``.  Returns the data and the
    strict compile result (the server keeps it for live loader queries).
    """
    summary = (summary or "").strip() or None
    strict = compile_turns(turns, source)
    ablation = compile_turns(turns, source, global_kinds=frozenset({"constraint"}))
    configs = {"strict": strict, "ablation": ablation}

    def gold_rows(group):
        return [{"desc": g["desc"], "summary": _has(summary, g["match"]) if summary else None,
                 **{k: {"in_header": _has(r.header, g["match"]), **_fate(r, g["match"])} for k, r in configs.items()}}
                for g in gold.get(group, [])]

    data = {
        "id": meta.get("id"), "title": meta.get("title", Path(source).name), "note": meta.get("note", ""),
        "turns": [{"i": t.index, "role": t.role} for t in turns],
        "lexemes": _lexemes(strict),
        "summary": summary,
        "tokens": {"full": count_tokens(render(turns)), "summary": count_tokens(summary) if summary else None,
                   "strict": count_tokens(strict.header), "ablation": count_tokens(ablation.header)},
        "gold": {"live": gold_rows("expected_live"), "dropped": gold_rows("expected_dropped"),
                 "stale": gold_rows("stale_values")} if gold else None,
        "configs": {k: _config(r) for k, r in configs.items()},
        "queries": queries or [],
        "probes": run_probes(strict.bundle, summary, probes) if probes else None,
        "llm": llm_compare(turns, source, strict, summary, probes) if llm else None,
    }
    return data, strict


def sample_files(sid: str) -> dict:
    """Raw inputs of a bundled sample."""
    base = ROOT / "samples"
    q = base / f"{sid}_queries.txt"
    pr = base / f"{sid}_probes.json"
    return {
        "probes": load_probes(pr) if pr.exists() else None,
        "chat": base / f"{sid}_chat.txt",
        "summary": (base / f"{sid}_summary.txt").read_text(encoding="utf-8"),
        "gold": json.loads((base / f"{sid}_gold.json").read_text(encoding="utf-8")),
        "queries": [l.strip() for l in q.read_text(encoding="utf-8").splitlines() if l.strip()] if q.exists() else [],
    }


def build_sample(spec: dict) -> dict:
    f = sample_files(spec["id"])
    data, _ = analyze_chat(load_transcript(f["chat"]), str(f["chat"]), f["summary"], f["gold"], f["queries"],
                           f["probes"], llm=True, **spec)
    return data


def build(out: str | Path = ROOT / "results" / "dashboard.html", fragment: str | Path | None = None,
          detailed: bool = False) -> Path:
    data = {"tokenizer": BACKEND, "samples": [build_sample(s) for s in SAMPLES]}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = (DETAILED_TEMPLATE if detailed else TEMPLATE).read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Standalone file for local use; the template itself has no <html>/<body>
    # wrapper so it can also be published as a hosted page fragment.
    out.write_text("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                   "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
                   "</head><body>\n" + page + "\n</body></html>\n", encoding="utf-8")
    if fragment:
        Path(fragment).write_text(page, encoding="utf-8")
    return out
