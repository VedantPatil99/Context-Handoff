"""Scope Stack Builder.

Reconstructs the conversation's task hierarchy as a tree of nested scopes,
maintained with an explicit stack exactly like the activation-record /
block-scope stack of a compiler front end:

* ``open_nested``  - a sub-task begins inside the current task (block entry)
* ``transition``   - the conversation moves on to the next phase: the current
                     phase is closed and a sibling opened (leave + enter block)
* ``close``        - the current task is declared finished (block exit)
* ``return_to``    - an earlier task is resumed: the stack is unwound / rebuilt
                     so that task is innermost again

The root scope ``s0`` (the whole conversation) is never closed.  At the end
of the transcript the stack holds the *active scope chain* that the
reachability analysis uses as its root set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Scope:
    id: str
    name: str
    parent: str | None
    depth: int
    opened_at: str            # lexeme loc
    closed_at: str | None = None
    children: list[str] = field(default_factory=list)
    reopened: int = 0
    abandoned: bool = False   # left unfinished ("too complicated, come back later"): rollback, not commit

    @property
    def closed(self) -> bool:
        return self.closed_at is not None


_NAME_STOP = re.compile(r"^(?:the|a|an|our|my|this|that|some|now)\s+", re.I)


def clean_scope_name(name: str) -> str:
    name = re.sub(r"[*_`\"]", "", name).strip()
    name = re.split(r"[.!?;:]|,\s*(?:and|then|so|which|where|we|i|you|it|but|if)\b|\s+(?:and then|so that|because)\s+", name)[0]
    prev = None
    while prev != name:
        prev, name = name, _NAME_STOP.sub("", name).strip()
    words = name.split()
    return " ".join(words[:6]).rstrip(",").strip() or "untitled"


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


class ScopeTree:
    def __init__(self, root_name: str = "conversation"):
        self.scopes: dict[str, Scope] = {}
        self.stack: list[str] = []
        self.events: list[dict] = []
        root = self._new(root_name, None, "t0.0")
        self.stack.append(root.id)

    # -- construction -------------------------------------------------
    def _new(self, name: str, parent: str | None, loc: str) -> Scope:
        sid = f"s{len(self.scopes)}"
        depth = 0 if parent is None else self.scopes[parent].depth + 1
        sc = Scope(sid, name, parent, depth, loc)
        self.scopes[sid] = sc
        if parent is not None:
            self.scopes[parent].children.append(sid)
        return sc

    @property
    def current(self) -> Scope:
        return self.scopes[self.stack[-1]]

    def chain(self) -> list[str]:
        """Active scope chain, innermost first (lexical lookup order)."""
        return list(reversed(self.stack))

    def _log(self, op: str, sid: str, loc: str):
        self.events.append({"op": op, "scope": sid, "loc": loc})

    def open_nested(self, name: str, loc: str) -> Scope:
        name = clean_scope_name(name)
        existing = self.find(name)
        if existing and existing.id in self.stack:
            return existing
        sc = self._new(name, self.current.id, loc)
        self.stack.append(sc.id)
        self._log("open", sc.id, loc)
        return sc

    def transition(self, name: str, loc: str) -> Scope:
        name = clean_scope_name(name)
        existing = self.find(name)
        if existing is not None:
            return self.return_to(existing.id, loc)
        if len(self.stack) > 1:
            self.close(loc)
        sc = self._new(name, self.current.id, loc)
        self.stack.append(sc.id)
        self._log("open", sc.id, loc)
        return sc

    def close(self, loc: str, name: str | None = None) -> Scope | None:
        if name:
            target = self.find(clean_scope_name(name))
            if target is not None and target.id in self.stack[1:]:
                while self.stack[-1] != target.id:
                    self._close_top(loc)
                return self._close_top(loc)
        if len(self.stack) > 1:
            return self._close_top(loc)
        return None

    def _close_top(self, loc: str) -> Scope:
        sid = self.stack.pop()
        self.scopes[sid].closed_at = loc
        self._log("close", sid, loc)
        return self.scopes[sid]

    def return_to(self, sid: str, loc: str) -> Scope:
        target = self.scopes[sid]
        if sid in self.stack:
            while self.stack[-1] != sid:
                self._close_top(loc)
            return target
        # Unwind to the deepest common ancestor, then re-enter the path.
        path = []
        cur: str | None = sid
        while cur is not None and cur not in self.stack:
            path.append(cur)
            cur = self.scopes[cur].parent
        while self.stack[-1] != cur:
            self._close_top(loc)
        for p in reversed(path):
            sc = self.scopes[p]
            sc.closed_at = None
            sc.abandoned = False
            sc.reopened += 1
            self.stack.append(p)
            self._log("reopen", p, loc)
        return target

    # -- lookup -------------------------------------------------------
    def find(self, name: str) -> Scope | None:
        """Find a non-root scope by (fuzzy) name; most recent match wins."""
        want = _words(name)
        if not want:
            return None
        best, best_score = None, 0.0
        for sc in list(self.scopes.values())[1:]:
            have = _words(sc.name)
            if not have:
                continue
            score = len(want & have) / len(want | have)
            if score >= best_score and score >= 0.5:
                best, best_score = sc, score
        return best

    def path(self, sid: str) -> str:
        names = []
        cur: str | None = sid
        while cur is not None:
            names.append(self.scopes[cur].name)
            cur = self.scopes[cur].parent
        return " > ".join(reversed(names))
