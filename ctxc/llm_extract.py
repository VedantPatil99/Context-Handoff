"""LLM-assisted front end: the rule-based extractor plus a grounded LLM pass.

The rule grammar runs first, exactly as in ``extractor.FrontEnd``.  After each
user turn, a local LLM (Ollama) is shown the turn's sentences with their
source locations, the previous assistant message, the facts already in the
symbol table and what the rules recorded for this turn.  It may emit only
three kinds of event, in a fixed JSON schema:

    {"events": [{"op": "define" | "revise" | "retract",
                 "symbol": "<existing symbol> or <new_snake_case_name>",
                 "value": "<fact value>",
                 "loc": "t9.1",
                 "quote": "<exact words copied from that sentence>"}]}

Every event is validated before it reaches the symbol table (G-rules below);
rejected events are kept with the reason, so the LLM step is as auditable as
the rules.  The LLM never sees the rest of the pipeline: scopes,
reachability, the constant pool and the loader are unchanged.

    G-LOC     loc must be a sentence of this user turn
    G-QUOTE   at least 80% of the quote's words must occur in the user's message
    G-VALUE   every number in the value must occur in the quote, and at least
              half of the value's content words must occur in the sentence or
              the previous assistant message
    G-TARGET  revise/retract must name a live symbol
    G-DUP     define must not repeat a value already in the symbol table

Raw model output is cached by prompt hash (``results/llm_cache``) so a run is
reproducible and the dashboard does not need the model to be running.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from .extractor import Event, FrontEnd, FrontEndResult
from .lexer import Lexeme
from .symtab import content_words, norm_value, similar

MODEL = "qwen2.5:7b"
OLLAMA = "http://127.0.0.1:11434/api/generate"
CACHE = Path(__file__).resolve().parent.parent / "results" / "llm_cache"

PROMPT = """You are the fact-extraction front end of a compiler that turns a chat into a symbol table.
A new assistant will later continue this chat from the symbol table alone, so it must hold every durable
fact the user stated or agreed to: values, decisions, requirements, who does what, how things work.

KNOWN FACTS (symbol = current value):
{known}

PREVIOUS ASSISTANT MESSAGE (context only, do not extract from it unless the user agrees to it):
{prev}

USER MESSAGE, one sentence per line with its location:
{sentences}

THE RULE EXTRACTOR ALREADY RECORDED FOR THIS MESSAGE:
{rules}

Task: list EVERY durable fact the user states or agrees to in this message, as operations:
- "define": a fact that is not in KNOWN FACTS. Give a short snake_case symbol name. Include descriptive
  facts with no number: requirements, roles, criteria, lists, how something works.
- "revise": the message changes the value of a KNOWN FACT, including through words like "it" or "that"
  ("we moved it to 21 February" revises the known date). Use the ORIGINAL known symbol name, even if the rule
  extractor recorded the new value as a separate fact.
- "retract": the user cancels or drops a KNOWN FACT (a change of value is "revise", not "retract").
  Use the known symbol name; value = what was dropped.
Never output questions, greetings, thanks, or opinions. Facts already known with the same value may be skipped.
"quote" must be copied exactly from the message, and "loc" must be the location of the sentence it comes from.

Example: for "[t40.0] judging will be on design and impact" the answer is
{{"events": [{{"op": "define", "symbol": "judging_criteria", "value": "design and impact", "loc": "t40.0", "quote": "judging will be on design and impact"}}]}}

Answer with JSON only:
{{"events": [{{"op": "define", "symbol": "name", "value": "value", "loc": "t1.0", "quote": "exact words"}}]}}
If nothing was missed, answer {{"events": []}}."""


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower().replace("'", "").replace("’", "")))


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+", s.replace(",", "")))


def call_llm(prompt: str, model: str = MODEL) -> str:
    """Ollama call at temperature 0, cached by (model, prompt) hash."""
    key = hashlib.sha256((model + "\n" + prompt).encode("utf-8")).hexdigest()[:24]
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0, "seed": 0, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())["response"]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "prompt": prompt, "response": resp}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return resp


class LLMFrontEnd(FrontEnd):
    def __init__(self, model: str = MODEL):
        super().__init__()
        self.model = model
        self.llm_log: list[dict] = []        # every proposed event, accepted or rejected
        self._turn: list[Lexeme] = []
        self._ev_start = 0
        self._prev_assistant = ""

    def feed(self, lx: Lexeme):
        if self._turn and lx.turn != self._turn[0].turn:
            self.flush()
        if not self._turn:
            self._ev_start = len(self.events)
        self._turn.append(lx)
        super().feed(lx)

    def flush(self):
        turn, self._turn = self._turn, []
        if not turn:
            return
        if turn[0].role != "user":
            self._prev_assistant = " ".join(l.text for l in turn if l.kind != "CODE")
            return
        sents = {l.loc: l for l in turn if l.kind != "CODE"}
        known = [f"- {s.name} = {s.current.value}" for s in self.symtab if not s.retracted]
        rules = [f"- {e.loc} {e.op} {e.ssa}" + (f" = {self._value(e.ssa)}" if e.op in ("define", "revise") else "")
                 for e in self.events[self._ev_start:] if e.op != "use"]
        prompt = PROMPT.format(known="\n".join(known) or "(none yet)",
                               prev=self._prev_assistant[:1500] or "(none)",
                               sentences="\n".join(f"[{loc}] {l.text}" for loc, l in sents.items()),
                               rules="\n".join(rules) or "(nothing)")
        try:
            events = json.loads(call_llm(prompt, self.model)).get("events", [])
        except (ValueError, AttributeError):
            events = []
        for ev in events if isinstance(events, list) else []:
            if isinstance(ev, dict):
                self._apply(ev, sents, turn)

    def _value(self, ssa: str) -> str:
        name, _, n = ssa.rpartition(".")
        sym = self.symtab.symbols.get(name)
        return sym.versions[int(n) - 1].value if sym and n.isdigit() and int(n) <= len(sym.versions) else ""

    def _apply(self, ev: dict, sents: dict[str, Lexeme], turn: list[Lexeme]):
        op = str(ev.get("op", "")).lower()
        name = str(ev.get("symbol", "")).strip()
        value = str(ev.get("value", "")).strip()
        loc = str(ev.get("loc", "")).strip()
        quote = str(ev.get("quote", "")).strip()
        rec = {"op": op, "symbol": name, "value": value, "loc": loc, "quote": quote, "turn": turn[0].turn}

        def reject(rule, why):
            self.llm_log.append({**rec, "accepted": False, "rule": rule, "why": why})

        if op not in ("define", "revise", "retract"):
            return reject("G-OP", f"unknown operation {op!r}")
        qw = _norm(quote).split()
        if not qw:
            return reject("G-QUOTE", "no quote given")
        whole = set(_norm(" ".join(l.text for l in sents.values())).split())
        if sum(w in whole for w in qw) < 0.8 * len(qw):
            return reject("G-QUOTE", "quote is not in the user's message")
        lx = sents.get(loc)
        if lx is None or not set(qw) & set(_norm(lx.text).split()):
            # Small models sometimes cite the wrong sequence number; use the sentence sharing most quote words.
            lx = max(sents.values(), key=lambda l: sum(w in _norm(l.text).split() for w in qw))
            rec["loc"] = loc = lx.loc
        if op in ("define", "revise"):
            if not value:
                return reject("G-VALUE", "empty value")
            if not _numbers(value) <= _numbers(quote + " " + lx.text):
                return reject("G-VALUE", "value has a number that is not in the quote")
            cw = content_words(value)
            src = set(content_words(lx.text + " " + self._prev_assistant))
            if cw and sum(w in src for w in cw) * 2 < len(cw):
                return reject("G-VALUE", "value is not grounded in the sentence")

        target = self.symtab.symbols.get(name)
        if op in ("revise", "retract"):
            if target is not None and target.retracted:
                return reject("G-DUP", "already cancelled")
            if target is None:
                return reject("G-TARGET", "names no live symbol")
            if op == "revise" and (similar(target.current.value, value) or
                                   set(content_words(value)) <= set(content_words(target.current.value))):
                return reject("G-DUP", "same value as the current version, only reworded")
            if op == "retract":
                self.symtab.retract(target, loc)
                target.retract_phrase = value or target.current.value
                self.events.append(Event(loc, "retract", target.current.ssa, "L-LLM-RETRACT"))
            else:
                self._merge_duplicate(loc, value)
                self._assign(lx, None, value, target.kind, "L-LLM-REVISE", target=target)
            rec["ssa"] = target.current.ssa
        else:
            if self._known(value, loc):
                return reject("G-DUP", "value already in the symbol table")
            kind = "constraint" if re.search(r"\b(must|can.?t|cannot|max|maximum|at most|limit|only)\b",
                                            value + " " + lx.text, re.I) else "fact"
            if not self._assign(lx, name.replace("_", " "), value, kind, "L-LLM-DEFINE", new_symbol=True):
                return reject("G-VALUE", "value or name rejected by the symbol table")
            rec["ssa"] = self.events[-1].ssa
        self.llm_log.append({**rec, "accepted": True, "rule": f"L-LLM-{op.upper()}"})

    def _known(self, value: str, loc: str) -> bool:
        """Is this value already held by a symbol (the rules usually word it differently)?"""
        vw, nv = set(content_words(value)), norm_value(value)
        for s in self.symtab:
            sv = s.current.value
            sw = set(content_words(sv))
            if similar(sv, value) or nv in norm_value(sv) or norm_value(sv) in nv:
                return True
            if vw and sw and (vw <= sw or sw <= vw or len(vw & sw) / len(vw | sw) >= 0.5):
                return True
            if s.current.loc == loc and vw and len(vw & sw) / len(vw) >= 0.34:
                return True
        return False

    def _merge_duplicate(self, loc: str, value: str):
        """A rule may have defined a *new* symbol for what the LLM recognises as a
        revision ("we moved it to 21 February").  Remove that duplicate so the
        value lives on as a new SSA version of the original symbol instead."""
        for e in list(self.events):
            if e.loc == loc and e.op == "define":
                name = e.ssa.rpartition(".")[0]
                sym = self.symtab.symbols.get(name)
                if sym and len(sym.versions) == 1 and similar(sym.current.value, value, 0.5):
                    del self.symtab.symbols[name]
                    self.symtab.order.remove(name)
                    self.events[:] = [x for x in self.events if x.ssa.rpartition(".")[0] != name]
                    self.llm_log.append({"op": "merge", "symbol": name, "value": sym.current.value, "loc": loc,
                                         "quote": "", "accepted": True, "rule": "L-LLM-MERGE",
                                         "why": "rule-made duplicate folded into the revised symbol"})


def run_llm_front_end(lexemes: list[Lexeme], model: str = MODEL) -> FrontEndResult:
    fe = LLMFrontEnd(model)
    for lx in lexemes:
        fe.feed(lx)
    fe.flush()
    res = FrontEndResult(lexemes, fe.symtab, fe.scopes, fe.discards, fe.events)
    res.llm_log = fe.llm_log
    return res
