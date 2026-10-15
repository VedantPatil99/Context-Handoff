"""Follow-up question benchmark: does the handoff lose context?

For every probe question, build exactly the context a new session would have:

* **summary**   - the prose summary text
* **ir**        - the IR header plus whatever the loader fetches for this
                  question (a fresh loader session per question: the IR's
                  intended protocol)
* **header**    - the IR header alone, to show what the loader contributes

and score whether the question can be answered from that context.

Value questions (``answer`` / optional ``stale``):
    answerable  an expected answer string is present
    wrong       only an outdated (stale) value is present
    no info     neither

"Is X still planned?" questions (``not_current``: keyword):
    answerable  the context marks X as dropped / cancelled / deferred
    wrong       X appears as if it were still current
    no info     X is not mentioned

This is an *answerability* check - whether the needed information reaches the
new session - not a test of whether a model would answer correctly.  For the
IR, a fetched dropped symbol counts as "marked not current", because the
loader prints the rule that eliminated it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .loader import ContextLoader
from .tokens import count_tokens

_NEG = re.compile(r"\b(?:dropped|drop|skipped|skip|deferred|defer|cancel(?:led|ed)?|removed|no longer|not|instead|"
                  r"stretch goal|only|abandoned|retracted|scrapped)\b", re.I)


def _has(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _score_value(ctx: str, p: dict) -> str:
    if _has(ctx, p["answer"]):
        return "answerable"
    if p.get("stale") and _has(ctx, p["stale"]):
        return "wrong"
    return "no info"


def _score_summary_status(summary: str, kw: str) -> str:
    sents = [s for s in re.split(r"(?<=[.!?])\s+", summary) if kw.lower() in s.lower()]
    if not sents:
        return "no info"
    return "answerable" if any(_NEG.search(s) for s in sents) else "wrong"


def _score_ir_status(header: str, fetched: list, kw: str) -> str:
    if kw.lower() in header.lower():
        return "wrong"                      # present in the header = presented as current
    hits = [r for r in fetched if kw.lower() in r.text.lower()]
    if not hits:
        return "no info"
    return "answerable" if all(r.status == "dropped" for r in hits) else "wrong"


def load_probes(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["probes"]


def run_probes(bundle: dict, summary: str | None, probes: list[dict]) -> dict:
    header = ContextLoader(bundle).header
    h_tok = count_tokens(header)
    s_tok = count_tokens(summary) if summary else None
    rows = []
    for p in probes:
        loader = ContextLoader(bundle)             # fresh session per question
        fetched = loader.on_query(p["q"])
        ir_ctx = header + "\n" + "\n".join(r.text for r in fetched)
        if "not_current" in p:
            kw = p["not_current"]
            ir = _score_ir_status(header, fetched, kw)
            hd = "wrong" if kw.lower() in header.lower() else "no info"
            sm = _score_summary_status(summary, kw) if summary else None
        else:
            ir, hd = _score_value(ir_ctx, p), _score_value(header, p)
            sm = _score_value(summary, p) if summary else None
        rows.append({"id": p["id"], "q": p["q"], "kind": "status" if "not_current" in p else "value",
                     "summary": sm, "ir": ir, "header": hd,
                     "fetched": [{"symbol": r.symbol, "status": r.status} for r in fetched],
                     "ir_tokens": h_tok + sum(r.tokens for r in fetched)})

    def tally(key):
        out = {"answerable": 0, "wrong": 0, "no info": 0}
        for r in rows:
            if r[key] is not None:
                out[r[key]] += 1
        return out

    n = len(rows)
    return {
        "rows": rows, "n": n,
        "totals": {"summary": tally("summary") if summary else None, "ir": tally("ir"), "header": tally("header")},
        "tokens": {"summary": s_tok, "header": h_tok,
                   "ir_avg": round(sum(r["ir_tokens"] for r in rows) / n) if n else h_tok},
    }
