"""Evaluation harness: Context IR vs. full transcript vs. prose-summary compaction.

Measures, on one transcript:

1. **Token cost** of each handoff artifact (same counter for all).
2. **Fact retention** against a hand-written gold list: is each expected
   fact carried by the artifact?  For IR misses the harness says *why*
   (never extracted vs. extracted-then-eliminated by rule X) - which is itself
   a demonstration of auditability.
3. **Auditability**: the IR's complete drop log (every eliminated symbol with
   its reachability rule) and per-lexeme fate accounting, versus the prose
   summary, for which no such list exists in the artifact.
4. **Loader behaviour**: header cost plus on-demand expansion cost for a set
   of follow-up queries.

The prose summary is supplied as a file; nothing here calls an LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .loader import ContextLoader
from .probe import load_probes, run_probes
from .pipeline import CompileResult, compile_turns
from .tokens import BACKEND, count_tokens
from .transcript import load_transcript, render


def _has(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


@dataclass
class IRVariant:
    label: str
    result: CompileResult

    @property
    def header(self) -> str:
        return self.result.header


def _ir_fate(variant: IRVariant, needles: list[str]) -> str:
    """Explain what the compiler did with a gold fact."""
    b = variant.result.bundle
    if _has(variant.header, needles):
        for name, sym in b["symbols"].items():
            if sym["live"] and _has(sym["versions"][-1]["value"] + " " + name, needles):
                return f"carried (`{name}`, {sym['live_rule']})"
        return "carried (mentioned in header)"
    for d in b["audit"]["dropped"]:
        if _has(d["value"] + " " + d["symbol"], needles):
            return f"dropped `{d['ssa']}` by **{d['rule']}**"
    for name, sym in b["symbols"].items():
        if any(_has(v["value"], needles) for v in sym["versions"]):
            return f"extracted as `{name}` but not in header"
    return "**not extracted** (front-end miss)"


def evaluate(transcript_path: str | Path, summary_path: str | Path, gold_path: str | Path | None = None,
             queries: list[str] | None = None, probes_path: str | Path | None = None) -> tuple[str, dict]:
    turns = load_transcript(transcript_path)
    full = render(turns)
    summary = Path(summary_path).read_text(encoding="utf-8").strip()
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8")) if gold_path else None

    strict = IRVariant("IR (strict, as proposed)", compile_turns(turns, str(transcript_path)))
    ablate = IRVariant("IR (ablation: constraints global)",
                       compile_turns(turns, str(transcript_path), global_kinds=frozenset({"constraint"})))
    variants = [strict, ablate]

    t_full, t_sum = count_tokens(full), count_tokens(summary)
    t_bundle = count_tokens(json.dumps(strict.result.bundle, ensure_ascii=False))
    rows = [("Full transcript", t_full), ("Prose summary (LLM-style compaction)", t_sum)]
    rows += [(v.label + " header", count_tokens(v.header)) for v in variants]

    data: dict = {"tokenizer": BACKEND, "tokens": dict(rows), "bundle_tokens_not_loaded": t_bundle}
    md: list[str] = [f"# Evaluation: `{Path(transcript_path).name}`", ""]
    md += [f"Tokenizer: {BACKEND}.  Turns: {len(turns)}.  "
           f"Lexemes: {len(strict.result.front.lexemes)}.", ""]

    # 1. token cost -------------------------------------------------------
    md += ["## 1. Token cost of the handoff artifact", "",
           "| Artifact | Tokens | % of transcript |", "|---|---:|---:|"]
    for label, n in rows:
        md.append(f"| {label} | {n} | {100 * n / t_full:.1f}% |")
    md += ["", f"The IR bundle (full symbol table + audit log + original text, {t_bundle} tokens) is *not* "
           "loaded up front; the loader reads from it only on demand.", ""]

    # 2. fact retention ---------------------------------------------------
    if gold:
        md += ["## 2. Fact retention against the hand-written gold list", "",
               "Gold list written before the pipeline was run; substring match, case-insensitive.", ""]
        hdr = "| Gold fact | Prose summary | " + " | ".join(v.label for v in variants) + " |"
        md += [hdr, "|---|---|" + "---|" * len(variants)]
        live_scores = {"summary": 0, **{v.label: 0 for v in variants}}
        for g in gold["expected_live"]:
            s_ok = _has(summary, g["match"])
            live_scores["summary"] += s_ok
            cells = []
            for v in variants:
                ok = _has(v.header, g["match"])
                live_scores[v.label] += ok
                cells.append(("✅ " if ok else "❌ ") + _ir_fate(v, g["match"]))
            md.append(f"| {g['desc']} | {'✅' if s_ok else '❌'} | " + " | ".join(cells) + " |")
        n = len(gold["expected_live"])
        md += ["", "**Expected-live facts carried:** " + ", ".join(
            f"{k}: {v}/{n}" for k, v in live_scores.items()), ""]
        data["live_recall"] = {k: f"{v}/{n}" for k, v in live_scores.items()}

        md += ["### Facts that should NOT be carried as current", "",
               "| Item | Prose summary | " + " | ".join(v.label for v in variants) + " |",
               "|---|---|" + "---|" * len(variants)]
        for group, title in (("expected_dropped", "dropped"), ("stale_values", "stale")):
            for g in gold.get(group, []):
                s = "mentioned" if _has(summary, g["match"]) else "absent"
                cells = [("mentioned" if _has(v.header, g["match"]) else "absent") + " — " + _ir_fate(v, g["match"])
                         for v in variants]
                md.append(f"| ({title}) {g['desc']} | {s} | " + " | ".join(cells) + " |")
        md += ["", "A prose summary that mentions a stale or abandoned item is not necessarily wrong (it may say "
               "\"switched from X\"), but the reader must parse prose to know which value is current; in the IR "
               "the stale version is simply not in the header and its SSA successor is.", ""]

    # 3. auditability -----------------------------------------------------
    b = strict.result.bundle
    md += ["## 3. Auditability", "", f"### 3a. Context IR — every eliminated symbol ({strict.label})", "",
           "| Symbol version | Value | Rule | Justification |", "|---|---|---|---|"]
    for d in b["audit"]["dropped"]:
        md.append(f"| `{d['ssa']}` | {d['value']} | {d['rule']} | {d['justification']} |")
    dc = b["audit"]["discard_counts"]
    n_lex = len(strict.result.front.lexemes)
    n_events = len({e["loc"] for e in b["audit"]["events"]})
    n_disc = len(b["audit"]["discarded"])
    md += ["", "Extraction-stage discards (lexemes that never became symbols), by rule: " +
           ", ".join(f"{k} {v}" for k, v in sorted(dc.items())), "",
           f"Fate accounted for: **{n_events + n_disc} / {n_lex} lexemes** "
           f"({n_events} produced a symbol/scope event, {n_disc} discarded with a named rule). "
           "Each is listed with its source location in the bundle's `audit` section.", ""]

    md += ["### 3b. Prose summary — the equivalent list", "",
           "The summary is free text. It contains no record of what it omitted, no link from any sentence to "
           "the turn it came from, and no rule explaining an omission. Concretely:", "",
           "- Drop list in the artifact: **none** — it cannot be produced from the summary alone.",
           f"- Lexemes whose fate can be determined from the artifact: **0 / {n_lex}**.",
           "- Summary sentences carrying a source pointer: **0**."]
    if gold:
        missing = [g["desc"] for g in gold["expected_live"] if not _has(summary, g["match"])]
        md.append("- Gold facts missing from the summary (found only by checking against the external gold list, "
                  "not from the summary itself): " + ("; ".join(missing) if missing else "none"))
    md.append("")

    # 4. loader -----------------------------------------------------------
    if queries:
        loader = ContextLoader(b)
        md += ["## 4. Loader: on-demand resolution", "",
               f"Header loaded up front: {loader.stats.header_tokens} tokens.", "",
               "| Follow-up query | Symbols resolved (matched by → status) | Tokens added |", "|---|---|---:|"]
        for q in queries:
            res = loader.on_query(q)
            cell = ", ".join(f"`{r.symbol}` ({r.matched_by} → {r.status})" for r in res) or "— (none referenced)"
            md.append(f"| {q} | {cell} | {sum(r.tokens for r in res)} |")
        md += ["", f"Total after all queries: header {loader.stats.header_tokens} + expansions "
               f"{loader.stats.expanded_tokens} = {loader.stats.header_tokens + loader.stats.expanded_tokens} tokens "
               f"(vs. {t_full} for re-reading the transcript).", ""]
        data["loader"] = {"header": loader.stats.header_tokens, "expanded": loader.stats.expanded_tokens,
                          "resolved": loader.stats.resolved}

    # 5. follow-up questions ---------------------------------------------
    if probes_path:
        pr = run_probes(b, summary, load_probes(probes_path))
        T, n = pr["totals"], pr["n"]
        md += ["## 5. Can a new session still answer follow-up questions?", "",
               "Each question is asked to a fresh session that has only the handoff. Prose summary: the summary text. "
               "Context IR: the header plus what the loader fetches for that question. **Answerable** = the needed "
               "information is in that context; **wrong** = only an outdated value, or a cancelled item shown as "
               "current; **no info** = neither. This measures whether the information reaches the new session, not "
               "whether a model would then answer correctly.", "",
               "| | Answerable | Wrong | No info | Tokens given to the new session |", "|---|---:|---:|---:|---:|",
               f"| Prose summary | {T['summary']['answerable']}/{n} | {T['summary']['wrong']} | {T['summary']['no info']} | {pr['tokens']['summary']} |",
               f"| Context IR (header + loader) | {T['ir']['answerable']}/{n} | {T['ir']['wrong']} | {T['ir']['no info']} | {pr['tokens']['ir_avg']} on average |",
               f"| IR header alone | {T['header']['answerable']}/{n} | {T['header']['wrong']} | {T['header']['no info']} | {pr['tokens']['header']} |",
               "", "| Question | Prose summary | Context IR | Loader fetched |", "|---|---|---|---|"]
        for r in pr["rows"]:
            f = ", ".join(f"`{x['symbol']}` ({x['status']})" for x in r["fetched"]) or "—"
            md.append(f"| {r['q']} | {r['summary']} | {r['ir']} | {f} |")
        md.append("")
        data["probes"] = {"totals": T, "n": n, "tokens": pr["tokens"]}

    md += ["## 6. Context IR header (strict) as handed off", "", "```", strict.header.rstrip(), "```", ""]
    return "\n".join(md), data
