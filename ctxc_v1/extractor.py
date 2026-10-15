"""Fact / Symbol Extraction - the parser of the context compiler.

A single left-to-right pass over the lexeme stream, like a one-pass compiler
front end.  For every lexeme it:

1. applies *scope directives* ("let's move on to X", "back to X",
   "done with X") to the Scope Stack Builder, so every definition records the
   scope that was active when it was introduced;
2. classifies the lexeme and, for durable facts, emits a definition,
   revision (new SSA version), restatement or retraction into the symbol
   table;
3. scans the lexeme for *uses* of symbols defined earlier (by name or by
   current value) - these use sites are the edges the reachability analysis
   later walks.

Everything that does not become a symbol is still recorded with the rule
that discarded it (question, exploratory, acknowledgement, filler, code),
so the extraction stage is as auditable as the elimination stage.

Extraction is deliberately rule-based and deterministic: identical input
always yields an identical IR, and every symbol can be traced to the exact
pattern that produced it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexer import Lexeme
from .scopes import ScopeTree
from .symtab import Symbol, SymbolTable, content_words, norm_value, slug, words

F = re.IGNORECASE

# --------------------------------------------------------------------------
# Scope directives (user turns only - assistant prose is full of "next, let's
# look at ..." that are explanations, not task boundaries).
# --------------------------------------------------------------------------
_INTERJ = r"(?:(?:ok(?:ay)?|alright|all right|great|good|perfect|cool|thanks|nice|so|right)\b[,.!]*\s*)*"
_SCOPE_TAG = re.compile(r"^(?P<sub>SUB)?(?:SCOPE|TASK|PHASE)\s*[:\-]\s*(?P<name>.+)$")
_SCOPE_END = re.compile(r"^END(?:\s+(?:SCOPE|TASK|PHASE))?\b")
_TRANS_MAJOR = re.compile(
    _INTERJ + r"(?:moving on to|on to|now for|next topic:?|next up:?|(?:phase|part|step|stage) \d+\s*[:\-]?)\s+"
    r"(?:the |our |my )?(?P<name>.+)", F)
_TRANS = re.compile(
    _INTERJ + r"(?:now|next|then)?,?\s*"
    r"(?:let's|let us|we(?:'ll| will| should| need to| can)|i(?:'d like to| want to| need to)|time to)\s+"
    r"(?:now\s+|next\s+)?"
    r"(?:move on to|move to|turn to|go on to|proceed to|start(?: working)? on|start with|work on|focus on|"
    r"tackle|get to|begin with|begin|look at|do)\s+(?:the |our |my )?(?P<name>.+)", F)
_NESTED = re.compile(
    _INTERJ + r"(?:(?:first|before that|as part of (?:this|that|it)|within (?:this|that)|"
    r"for (?:this|that) (?:part|step|section)),?\s*(?:let's|we need to|we should|i want to)\s+"
    r"(?:first\s+)?(?:start with|do|work on|figure out|look at|handle|sort out|deal with)|"
    r"sub-?task\s*[:\-]?|let's (?:first )?(?:zoom in on|drill into|dig into|break out))\s+(?:the |our )?(?P<name>.+)", F)
_RETURN = re.compile(
    r"\b(?:back to|return(?:ing)? to|resume|resuming|going back to|revisit(?:ing)?)\s+(?:the |our |my )?(?P<name>[^,.;!?]+)", F)
_CLOSE = re.compile(
    _INTERJ + r"(?:(?:that's|that is|we're|we are|i'm|i am)\s+)?(?:done|finished|complete|completed)"
    r"(?:\s+with\s+(?:the\s+)?(?P<name>[^,.;!?]+))?\s*[.!]*$"
    r"|" + _INTERJ + r"that (?:wraps up|settles|finishes|concludes|completes)(?:\s+(?:the\s+)?(?P<name2>[^,.;!?]+))?", F)

# --------------------------------------------------------------------------
# Lexeme classification
# --------------------------------------------------------------------------
_ACK = re.compile(
    r"^(?:ok(?:ay)?|sure|thanks?|thank you|great|perfect|cool|nice|sounds good|got it|yes|yeah|yep|yup|"
    r"alright|all right|awesome|exactly|right|agreed|good|love it|looks good|lgtm|makes sense|"
    r"go (?:with|for) (?:it|that|this)|let's do (?:it|that|this)|do it|that works|works for me)\b(?:[\s,!.]+[\w']+){0,3}[\s!.]*$", F)
_POSITIVE_ACK = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|sounds good|perfect|great|agreed|love it|that works|works for me|"
    r"go (?:with|for) (?:it|that|this)|let's do (?:it|that|this)|do it|let's go with (?:that|it|this)|use that)\b", F)
_HEDGE = re.compile(
    r"\b(?:maybe|perhaps|what if|how about|what about|we could|could we|i wonder|not sure|might|possibly|"
    r"alternatively|one option|another option|i'm thinking|thinking about|considering|brainstorm)\b", F)
_PROPOSAL = re.compile(
    r"\b(?:i(?:'d| would)? (?:suggest|recommend|propose)|my recommendation is|how about|what about)"
    r"\s+(?:that\s+)?(?:we\s+|you\s+)?(?P<rest>.+)", F)
_TAG = re.compile(r"^\W*(?P<tag>DECISION|DECIDED|CONSTRAINT|RULE|FACT|NOTE|REQUIREMENT|PIN)\s*[:\-]\s*(?P<body>.+)$")
_SETTAG = re.compile(r"^\W*@set\s+(?P<key>[\w.\- ]+?)\s*=\s*(?P<val>.+)$", F)
_PIN = re.compile(
    r"^(?:please\s+)?(?:remember(?: that)?|important(?:ly)?\s*[:,\-]?|note(?: that)?\s*[:\-]?|"
    r"keep in mind(?: that)?|fyi\s*[:,\-]?|for the record\s*[:,\-]?)\s+(?P<rest>.+)", F)
_RETRACT = re.compile(
    r"^(?:" + _INTERJ + r")?(?:actually,?\s*)?(?:never ?mind(?: about)?|forget(?: about)?|scrap|drop|discard|cancel|ignore|"
    r"we no longer need|we don't need|remove|get rid of)\s+(?:the |our |my |that )?(?P<target>[^,.;!?]+)", F)
_SWITCH = [
    re.compile(r"\b(?:switch|change|move|migrate|go|swap)(?:ing)?\s+(?:it\s+|that\s+)?from\s+(?P<old>.+?)\s+to\s+(?P<val>.+)", F),
    re.compile(r"\breplace\s+(?P<old>.+?)\s+with\s+(?P<val>.+)", F),
    re.compile(r"\b(?:use|go with|pick|choose)\s+(?P<val>.+?)\s+instead of\s+(?P<old>.+)", F),
    re.compile(r"\binstead of\s+(?P<old>.+?),\s*(?:let's\s+|we'll\s+|we will\s+|i'll\s+)?(?:use|go with|pick|choose)\s+(?P<val>.+)", F),
]
_ASSIGN_VERBS_USER = r"change|switch|update|rename|revise|set|fix|bump|increase|decrease|reduce|extend|move|shorten|push|cap|limit"
_ASSIGN_VERBS_ASSIST = r"changed|updated|renamed|revised|set|moved|bumped|increased|decreased|reduced|extended|shortened|pushed|capped|limited"
_ASSIGN_USER = re.compile(
    r"^(?:" + _INTERJ + r")?(?:actually,?\s*)?(?:let's\s+|let us\s+|please\s+|we(?:'ll| will| should| need to)?\s+|i(?:'ll| will| want to)?\s+|can you\s+|could you\s+)?"
    r"(?:" + _ASSIGN_VERBS_USER + r")\s+(?:the |our |my )?(?P<key>[\w\s\-']{2,40}?)\s+(?:to|as|=|at)\s+(?P<val>.+)", F)
_ASSIGN_ASSIST = re.compile(
    r"^(?:" + _INTERJ + r")?(?:i(?:'ve| have)|we(?:'ve| have))\s+(?:now\s+)?(?:" + _ASSIGN_VERBS_ASSIST + r")\s+"
    r"(?:the |our |your )?(?P<key>[\w\s\-']{2,40}?)\s+(?:to|as|=|at)\s+(?P<val>.+)", F)
_COPULA_STRONG = r"will be|should be|shall be|is going to be|is now|are now|is set to|has been (?:changed|updated|set|moved) to|will now be"
_COPULA_USER = re.compile(
    r"^(?:" + _INTERJ + r")?(?:and |also,? |actually,? )?(?:the |our |my )(?P<key>[\w\s\-']{2,40}?)\s+"
    r"(?P<cop>" + _COPULA_STRONG + r"|is|are|=)\s+(?P<val>.+)", F)
_COPULA_ASSIST = re.compile(
    r"^(?:" + _INTERJ + r")?(?:the |our |your )(?P<key>[\w\s\-']{2,40}?)\s+(?P<cop>is now|are now|is set to|has been (?:changed|updated|set|moved) to)\s+(?P<val>.+)", F)
_CALL = re.compile(
    r"^(?:" + _INTERJ + r")?(?:let's|we(?:'ll| will| should)|i(?:'ll| will| want to))\s+(?:call|name|title)\s+"
    r"(?:it|this|the (?P<key>[\w\s\-]{2,30}?))\s+(?P<val>.+)", F)
_CHOICE = re.compile(
    r"^(?:" + _INTERJ + r")?(?:actually,?\s*)?(?:let's|let us|we(?:'ll| will| should| are going to| want to)?|i(?:'ll| will| want to| would like to|'d like to)?)\s+"
    r"(?:go with|use|pick|choose|stick with|adopt|opt for|settle on)\s+(?P<val>.+?)"
    r"(?:\s+(?:for|as)\s+(?:the |our |my )?(?P<key>[^,.;]{2,40}?))?\s*[.!]?$", F)
_CHOICE_FRONTED = re.compile(
    r"^for (?:the |our |my )?(?P<key>[^,]{2,30}?),?\s+(?:let's|we(?:'ll| will| should| are going to)?|i(?:'ll| will)?)\s+"
    r"(?:go with|use|pick|choose|stick with|adopt|opt for)\s+(?P<val>.+)", F)
_DECIDE = re.compile(
    r"^(?:" + _INTERJ + r")?(?:so,?\s*)?(?:we(?:'ve| have)?\s+|i(?:'ve| have)?\s+)?(?:decided|agreed|concluded|settled on|finali[sz]ed)"
    r"(?:\s+on|\s+to|\s+that)?\s+(?P<val>.+)", F)
_CONSTRAINT_SUBJ = re.compile(
    r"^(?:" + _INTERJ + r")?(?:and |also,? )?(?P<subj>(?:the |our |my |every |each |all |no )?[\w\s\-']{2,40}?)\s+"
    r"(?P<modal>must not|must|mustn't|cannot|can't|can not|should never|should not|shouldn't|should|has to|have to|needs? to|"
    r"is required to|are required to|may not|is limited to|are limited to)\s+(?P<pred>.+)", F)
_CONSTRAINT_IMPER = re.compile(
    r"^(?:" + _INTERJ + r")?(?:please\s+)?(?P<neg>never|always|don't|do not|avoid|make sure(?: to| that)?|ensure(?: that)?)\s+(?P<pred>.+)", F)
_CONSTRAINT_KW = re.compile(r"\b(?:deadline|due (?:on|by)|at most|at least|no more than|no less than|max(?:imum)?|min(?:imum)?|limit of|word limit|page limit)\b", F)
_KV = re.compile(r"^(?:\*\*)?(?P<key>[A-Za-z][\w \-/]{1,30}?)(?:\*\*)?\s*:\s*(?:\*\*)?(?P<val>[^:]{1,160}?)(?:\*\*)?$")

_PRONOUN_KEYS = {"it", "that", "this", "there", "he", "she", "they", "what", "which", "who", "one", "thing", "things", "you", "we", "i"}
_BAD_HEADS = {
    "problem", "issue", "thing", "question", "point", "idea", "reason", "answer", "difference", "way",
    "result", "output", "error", "code", "example", "goal of this", "main", "rest", "point is", "good news", "bad news",
    "on", "forward", "ahead", "back", "up", "away", "out", "down", "off",
}
_ADVERBS = {"really", "actually", "just", "also", "already", "still", "probably", "even", "now", "then", "too", "very", "quite"}
_BAD_KV_KEYS = {"note", "edit", "example", "question", "answer", "update", "user", "assistant", "human", "claude", "source", "output", "input", "error", "warning", "step", "tip"}
_VAL_CUT = re.compile(r"\s+(?:because|since|so that|as it|as this|which (?:is|means|should|will)|instead\b(?! of)|unless|but|though|although)\b.*$|\s+[-–—]\s+.*$|;.*$", F)


_LEAD = re.compile(
    r"^(?:(?:and|also|so|but|then|plus|ok(?:ay)?|yes|yeah|yep|sure|right|alright|great|good|cool|hmm+|well|actually)\b[,!.]?\s+)+", F)
_REASON = re.compile(r",?\s+(?:since|because|as it|as we|given that)\b.*$", F)


_INFORMAL = [
    (re.compile(r"(^|[,.!;:]\s*|\b(?:ok|okay|so|now|then|and|but|yeah|alright)\s+)lets\b", F), r"\1let's"),
    (re.compile(r"\b(do|does|is|are|was|were|ca|wo|should|would|could|have|has|had|did|must)nt\b", F), r"\1n't"),
    (re.compile(r"\bim\b", F), "I'm"),
    (re.compile(r"\b(that|what|it|there|here|who)s\b(?=\s)", F), r"\1's"),
]


def canon(text: str) -> str:
    """Restore apostrophes dropped in informal typing ("lets", "dont", "im",
    "thats") so the same grammar handles casual and tidy writing."""
    for rx, rep in _INFORMAL:
        text = rx.sub(rep, text)
    return text


def normalize(text: str) -> str:
    """Strip leading discourse markers ("And", "Yes,", "Actually,") and trailing
    reason clauses ("since ..."): they carry no fact but block the patterns."""
    t = _LEAD.sub("", text.strip())
    t = _REASON.sub("", t)
    return t[:1].upper() + t[1:] if t else t


def clean_value(v: str) -> str:
    v = v.strip()
    v = _VAL_CUT.sub("", v)
    v = re.sub(r"\s*\((?:i think|for now|probably|ideally)\)\s*", " ", v, flags=F)
    v = v.strip().rstrip(".!,:;").strip()
    if len(v) >= 2 and v[0] in "\"'“‘`*" and v[-1] in "\"'”’`*":
        v = v.strip("\"'“”‘’`*").strip()
    if len(v) > 160:
        v = v[:157].rsplit(" ", 1)[0] + "..."
    return v


def clean_key(k: str) -> str | None:
    k = re.sub(r"[*_`\"]", "", k).strip().lower()
    ws = k.split()
    if not ws or len(ws) > 5:
        return None
    if ws[0] in _PRONOUN_KEYS or k in _BAD_HEADS or ws[-1] in _BAD_HEADS:
        return None
    if any(w in _ADVERBS or (w.endswith("ly") and len(w) > 4) for w in ws):
        return None
    return k


def phrase_key(text: str, n: int = 3, prefix: str = "") -> str:
    skip = {"must", "never", "always", "should", "need", "needs", "make", "sure", "ensure", "avoid", "per", "via"}
    cw = [w for w in content_words(text) if w not in skip and w not in _PRONOUN_KEYS]
    return (prefix + " " + " ".join(cw[:n])).strip() or "fact"


# --------------------------------------------------------------------------
@dataclass
class Discard:
    loc: str
    rule: str      # E-QUESTION | E-EXPLORATORY | E-ACK | E-FILLER | E-CODE | E-PROPOSAL | E-NARRATIVE
    role: str


@dataclass
class Event:
    loc: str
    op: str        # define | revise | restate | retract | use
    ssa: str
    rule: str      # which extraction pattern fired


@dataclass
class FrontEndResult:
    lexemes: list[Lexeme]
    symtab: SymbolTable
    scopes: ScopeTree
    discards: list[Discard] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


class FrontEnd:
    def __init__(self):
        self.symtab = SymbolTable()
        self.scopes = ScopeTree()
        self.discards: list[Discard] = []
        self.events: list[Event] = []
        self._pending_proposal: tuple[str, Lexeme] | None = None
        self._defined_here: set[str] = set()
        self._last_role: str | None = None

    # -- helpers ------------------------------------------------------
    def _scope(self) -> str:
        return self.scopes.current.id

    def _assign(self, lx: Lexeme, key: str | None, val: str, kind: str, rule: str,
                *, target: Symbol | None = None, pinned: bool = False, new_symbol: bool = False) -> bool:
        val = clean_value(val)
        if target is None:
            key = clean_key(key) if key else None
            if not key:
                return False
        if not val or len(norm_value(val)) < 1 or len(val.split()) > 30:
            return False
        event, sym, ver = self.symtab.assign(key or target.name, val, kind, self._scope(), lx.loc,
                                             self.scopes.chain(), target=target, new_symbol=new_symbol)
        if pinned:
            sym.pinned = True
        ssa = ver.ssa if ver else sym.current.ssa
        self.events.append(Event(lx.loc, event, ssa, rule))
        self._defined_here.add(sym.name)
        return True

    def _discard(self, lx: Lexeme, rule: str):
        self.discards.append(Discard(lx.loc, rule, lx.role))

    # -- scope directives --------------------------------------------
    def _scope_directive(self, lx: Lexeme) -> bool:
        if lx.role != "user":
            return False
        text = canon(lx.text.strip())
        if lx.kind == "HEADING":
            self.scopes.transition(text, lx.loc)
            return True
        m = _SCOPE_TAG.match(text)
        if m:
            (self.scopes.open_nested if m.group("sub") else self.scopes.transition)(m.group("name"), lx.loc)
            return True
        if _SCOPE_END.match(text):
            self.scopes.close(lx.loc)
            return True
        m = _RETURN.search(text)
        if m:
            sc = self.scopes.find(m.group("name"))
            if sc is not None:
                self.scopes.return_to(sc.id, lx.loc)
                return True
        m = _CLOSE.match(text)
        if m:
            self.scopes.close(lx.loc, m.group("name") or m.group("name2"))
            return True
        m = _NESTED.match(text)
        if m and self._valid_scope_name(m.group("name")):
            self.scopes.open_nested(m.group("name"), lx.loc)
            return True
        m = _TRANS_MAJOR.match(text)
        if m and self._valid_scope_name(m.group("name")):
            while len(self.scopes.stack) > 2:
                self.scopes.close(lx.loc)
            self.scopes.transition(m.group("name"), lx.loc)
            return True
        m = _TRANS.match(text)
        if m and self._valid_scope_name(m.group("name")):
            self.scopes.transition(m.group("name"), lx.loc)
            return True
        return False

    @staticmethod
    def _valid_scope_name(name: str) -> bool:
        cw = content_words(name)
        return bool(cw) and cw[0] not in _PRONOUN_KEYS and words(name)[0] not in {"it", "that", "this", "them", "so"}

    # -- fact patterns (shared by direct, pinned and proposal paths) -----
    def _fact_patterns(self, lx: Lexeme, text: str, *, pinned: bool = False, via: str = "") -> bool:
        user = lx.role == "user"
        t = normalize(text)

        m = _SETTAG.match(t)
        if m:
            return self._assign(lx, m.group("key").replace(".", " "), m.group("val"), "fact", via + "P-SET", pinned=pinned)
        m = _TAG.match(t)
        if m:
            tag, body = m.group("tag").upper(), m.group("body")
            kind = {"DECISION": "decision", "DECIDED": "decision", "CONSTRAINT": "constraint", "RULE": "constraint",
                    "REQUIREMENT": "constraint"}.get(tag, "fact")
            if self._fact_patterns(lx, body, pinned=pinned or tag == "PIN", via=via + f"P-TAG({tag})+"):
                return True
            return self._assign(lx, phrase_key(body, prefix="rule" if kind == "constraint" else ""), body, kind,
                                via + f"P-TAG({tag})", pinned=pinned or tag == "PIN")

        if user:
            m = _RETRACT.match(t)
            if m:
                tgt = m.group("target")
                sym = self.symtab.resolve(tgt, self.scopes.chain()) if clean_key(tgt) else None
                sym = sym or self.symtab.find_by_value(tgt)
                if sym is not None and not sym.retracted:
                    self.symtab.retract(sym, lx.loc)
                    self.events.append(Event(lx.loc, "retract", sym.current.ssa, via + "P-RETRACT"))
                    self._defined_here.add(sym.name)
                    return True

        for rx in _SWITCH:
            m = rx.search(t)
            if m and (user or via):
                old = clean_value(m.group("old"))
                sym = self.symtab.find_by_value(old)
                if sym is not None:
                    return self._assign(lx, None, m.group("val"), sym.kind, via + "P-SWITCH", target=sym, pinned=pinned)
                key = self._choice_key(old)
                return self._assign(lx, key, m.group("val"), "choice", via + "P-SWITCH", pinned=pinned)

        m = (_ASSIGN_USER if user else _ASSIGN_ASSIST).match(t)
        if m and clean_key(m.group("key")):
            return self._assign(lx, m.group("key"), m.group("val"), "fact", via + "P-ASSIGN", pinned=pinned)

        m = _CALL.match(t)
        if m and (user or via):
            key = (m.group("key") or "") + " name" if m.group("key") else self._name_key()
            return self._assign(lx, key, m.group("val"), "decision", via + "P-NAME", pinned=pinned)

        m = (_COPULA_USER if user else _COPULA_ASSIST).match(t)
        if m and clean_key(m.group("key")):
            val = m.group("val")
            weak = m.group("cop").lower() in ("is", "are", "=")
            if not (weak and (len(val.split()) > 15 or re.match(r"(?:that|because|not sure|unclear|a bit|kind of|so|too|very|really|pretty)\b", val, F))):
                return self._assign(lx, m.group("key"), val, "fact", via + ("P-COPULA" if weak else "P-COPULA-STRONG"), pinned=pinned)

        m = _CHOICE.match(t)
        if m and (user or via):
            val = m.group("val")
            key = m.group("key") or self._choice_key(val)
            return self._assign(lx, key, val, "choice", via + "P-CHOICE", pinned=pinned)

        m = _CHOICE_FRONTED.match(t)
        if m and (user or via):
            return self._assign(lx, m.group("key"), m.group("val"), "choice", via + "P-CHOICE", pinned=pinned)

        m = _DECIDE.match(t)
        if m and (user or re.match(r"we\b", t, F)):
            val = m.group("val")
            return self._assign(lx, phrase_key(val), val, "decision", via + "P-DECIDE", pinned=pinned)

        if user or via:
            m = _CONSTRAINT_SUBJ.match(t)
            if m and clean_key(m.group("subj")) is not None:
                pred_cw = [w for w in content_words(m.group("pred")) if w not in {"be", "been", "use"}]
                key = clean_key(m.group("subj")) + " " + (pred_cw[0] if pred_cw else "rule")
                return self._assign(lx, key, t, "constraint", via + "P-CONSTRAINT", pinned=pinned)
            m = _CONSTRAINT_IMPER.match(t)
            if m:
                return self._assign(lx, phrase_key(m.group("pred"), prefix="rule"), t, "constraint",
                                    via + "P-CONSTRAINT-IMP", pinned=pinned)
            if _CONSTRAINT_KW.search(t) and re.search(r"\d", t) and len(t.split()) <= 30:
                return self._assign(lx, phrase_key(t), t, "constraint", via + "P-CONSTRAINT-KW", pinned=pinned)

        if user and not via:
            m = _KV.match(t)
            if m:
                key = clean_key(m.group("key"))
                if key and key.split()[0] not in _BAD_KV_KEYS and len(key.split()) <= 4:
                    return self._assign(lx, key, m.group("val"), "fact", "P-KV", pinned=pinned)
        return False

    def _choice_key(self, val: str) -> str:
        cur = self.scopes.current
        if cur.parent is not None:
            return " ".join(content_words(cur.name)[:3]) + " choice"
        return phrase_key(val, 2) + " choice"

    def _name_key(self) -> str:
        cur = self.scopes.current
        return f"{cur.name} name" if cur.parent is not None else "name"

    # -- use detection ------------------------------------------------
    def _detect_uses(self, lx: Lexeme):
        text_n = " " + norm_value(lx.text) + " "
        scope = self._scope()
        for sym in self.symtab:
            if sym.name in self._defined_here:
                continue
            phrase = " " + sym.name.replace("_", " ") + " "
            if len(sym.name) >= 4 and phrase in text_n and not sym.name.endswith(("_choice", "_name")):
                self.symtab.use(sym, lx.loc, scope, "name")
                continue
            vn = norm_value(sym.current.value)
            if (len(vn) >= 5 or (len(vn) >= 3 and lx.text.find(sym.current.value) >= 0)) and \
                    len(vn.split()) <= 8 and f" {vn} " in text_n:
                self.symtab.use(sym, lx.loc, scope, "value")

    # -- main loop ----------------------------------------------------
    def feed(self, lx: Lexeme):
        self._defined_here = set()
        if lx.role != self._last_role and lx.role == "assistant":
            self._pending_proposal = None  # a new assistant turn supersedes old proposals
        self._last_role = lx.role

        if lx.kind == "CODE":
            self._discard(lx, "E-CODE")
            return
        text = canon(lx.text.strip())

        is_scope = self._scope_directive(lx)
        handled = False

        if lx.role == "user" and lx.seq == 0 and self._pending_proposal and _POSITIVE_ACK.match(text):
            rest, plx = self._pending_proposal
            self._pending_proposal = None
            handled = self._fact_patterns(lx, "let's " + rest, via="P-ACCEPT+")
            if not handled:
                handled = self._assign(lx, phrase_key(rest), rest, "decision", "P-ACCEPT")

        if not handled and not is_scope:
            pm = _PIN.match(text) if lx.role == "user" else None
            if text.endswith("?"):
                self._discard(lx, "E-QUESTION")
            elif lx.role == "assistant" and (pm2 := _PROPOSAL.search(text)):
                self._pending_proposal = (pm2.group("rest").rstrip(".!"), lx)
                self._discard(lx, "E-PROPOSAL")
            elif _ACK.match(text):
                self._discard(lx, "E-ACK")
            elif _HEDGE.search(text) and not _TAG.match(text):
                self._discard(lx, "E-EXPLORATORY")
            elif pm:
                rest = pm.group("rest")
                handled = self._fact_patterns(lx, rest, pinned=True, via="P-PIN+") or \
                    self._assign(lx, phrase_key(rest), rest, "fact", "P-PIN", pinned=True)
            else:
                handled = self._fact_patterns(lx, text)
                if not handled:
                    self._discard(lx, "E-FILLER" if lx.role == "user" else "E-NARRATIVE")
        elif is_scope and not handled:
            self.events.append(Event(lx.loc, "scope", self._scope(), "S-DIRECTIVE"))

        self._detect_uses(lx)


def run_front_end(lexemes: list[Lexeme]) -> FrontEndResult:
    fe = FrontEnd()
    for lx in lexemes:
        fe.feed(lx)
    return FrontEndResult(lexemes, fe.symtab, fe.scopes, fe.discards, fe.events)
