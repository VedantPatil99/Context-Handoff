"""Tests for the compiler mechanics.  These pin down *behaviour of each pass*
(SSA, scopes, reachability rules, constant pool, loader), not evaluation
scores - the scores live in results/ and are reported as measured."""
import json
from pathlib import Path

import pytest

from ctxc.ir import read_bundle, render_header, write
from ctxc.loader import ContextLoader
from ctxc.pipeline import compile_file, compile_turns
from ctxc.scopes import ScopeTree
from ctxc.symtab import SymbolTable
from ctxc.transcript import Turn, load_transcript, parse_text

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def turns(*pairs):
    return [Turn(i, r, t) for i, (r, t) in enumerate(pairs)]


def compile_chat(*pairs, **kw):
    return compile_turns(turns(*pairs), **kw)


# --- symbol table / SSA -------------------------------------------------
def test_ssa_new_version_on_change_and_history_kept():
    st = SymbolTable()
    assert st.assign("deadline", "Nov 15", "fact", "s0", "t0.0", ["s0"])[0] == "define"
    ev, sym, v = st.assign("the deadline", "Nov 22", "fact", "s0", "t5.0", ["s0"])
    assert ev == "revise" and v.ssa == "deadline.2"
    assert [x.value for x in sym.versions] == ["Nov 15", "Nov 22"]
    assert sym.versions[0].killed_by == "deadline.2"


def test_restatement_is_a_use_not_a_version():
    st = SymbolTable()
    st.assign("database", "PostgreSQL", "choice", "s0", "t0.0", ["s0"])
    ev, sym, _ = st.assign("database", "postgresql", "choice", "s0", "t3.0", ["s0"])
    assert ev == "restate" and len(sym.versions) == 1 and sym.uses[-1].how == "restate"


def test_name_resolution_prefers_inner_scope():
    st = SymbolTable()
    st.assign("project title", "A", "fact", "s0", "t0.0", ["s0"])
    st.assign("report title", "B", "fact", "s2", "t1.0", ["s2", "s0"])
    assert st.resolve("title", ["s2", "s0"]).name == "report_title"
    assert st.resolve("title", ["s1", "s0"]).name == "project_title"


# --- scope stack --------------------------------------------------------
def test_scope_transition_close_and_return():
    sc = ScopeTree()
    a = sc.transition("requirements", "t1.0")
    b = sc.transition("tech stack", "t2.0")           # closes requirements
    assert sc.scopes[a.id].closed and sc.chain() == [b.id, "s0"]
    n = sc.open_nested("notifications", "t3.0")
    assert sc.chain() == [n.id, b.id, "s0"]
    sc.return_to(a.id, "t4.0")                        # unwinds b and n, reopens a
    assert sc.chain() == [a.id, "s0"] and sc.scopes[a.id].reopened == 1
    assert sc.scopes[n.id].closed and sc.scopes[b.id].closed


# --- reachability -------------------------------------------------------
CHAT = (
    ("user", "The deadline is November 15."),
    ("user", "Let's work on the tech stack."),
    ("user", "We'll use Express for the backend."),
    ("user", "Within this, let's first look at push notifications."),
    ("user", "Notifications should be sent within 5 minutes."),
    ("user", "This is getting complicated, let's move on to the data model."),   # abandons notifications
    ("user", "Ok we decided to add dark mode."),
    ("user", "The deadline is now November 22."),
    ("user", "Forget about dark mode."),
    ("user", "Moving on to testing."),   # major transition: pops back to top level
    ("assistant", "Run the Express app under PM2."),
)


def drops(result):
    return {d.ssa: d.rule for d in result.reach.dropped}


def test_reachability_rules():
    r = compile_chat(*CHAT)
    d = drops(r)
    assert d["deadline.1"] == "R-SSA"                     # dead store
    assert d["add_dark_mode.1"] == "R-KILL"               # retracted
    assert d["notifications_sent.1"] == "R-SCOPE"         # abandoned nested scope (rollback)
    live = r.reach.live
    assert live["deadline"].rule == "L-SCOPE"             # declared in root, revised in inner scope
    assert live["backend"].rule == "L-USE"                # escaped via a use in the active scope


def test_completed_scope_exports_its_symbols():
    chat = (("user", "Let's work on requirements."), ("user", "Every API response must be JSON."),
            ("user", "Done with requirements."), ("user", "Now let's work on testing."))
    r = compile_chat(*chat)
    assert r.reach.live["every_api_response_json"].rule == "L-EXPORT"     # commit, not rollback
    assert compile_chat(*chat).reach.dropped == []


def test_rejected_item_becomes_a_tombstone():
    r = compile_chat(("user", "Maybe we could add RSVP?"), ("user", "Yeah, skip RSVP for now."))
    assert {d.rule for d in r.reach.dropped} == {"R-KILL"} and "RSVP (cancelled)" in r.header


def test_correction_of_descriptive_fact_is_a_new_version():
    r = compile_chat(("user", "XGBoost got RMSE of 0.42 on the test set."),
                     ("user", "Wait, I rechecked and the XGBoost RMSE is actually 0.47."))
    assert {d.rule for d in r.reach.dropped} == {"R-SSA"} and "0.47" in r.header


def test_every_drop_has_a_justification():
    r = compile_chat(*CHAT)
    assert r.reach.dropped and all(d.rule and d.justification for d in r.reach.dropped)


def test_global_constraint_ablation_keeps_constraints():
    r = compile_chat(*CHAT, global_kinds=frozenset({"constraint"}))
    assert r.reach.live["notifications_sent"].rule == "L-GLOBAL"


def test_exploratory_and_questions_never_become_symbols():
    r = compile_chat(("user", "Maybe we could add an RSVP feature?"),
                     ("user", "What if we used Redis?"))
    assert len(r.front.symtab) == 0
    assert {d.rule for d in r.front.discards} == {"E-QUESTION"}


def test_every_lexeme_has_an_accounted_fate():
    r = compile_file(SAMPLES / "campus_events_chat.txt")
    b = r.bundle
    fated = {e["loc"] for e in b["audit"]["events"]} | {d["loc"] for d in b["audit"]["discarded"]}
    assert fated == set(b["store"])


# --- constant pool ------------------------------------------------------
def test_constant_pool_holds_only_single_version_live_symbols():
    r = compile_chat(*CHAT)
    const_syms = {s for c in r.partition.constants for s in c.symbols}
    assert "backend" in const_syms and "deadline" not in const_syms
    assert "deadline" in r.partition.variables


# --- serializer + loader -----------------------------------------------
def test_roundtrip_and_lazy_resolution(tmp_path):
    r = compile_chat(*CHAT)
    hp, bp = write(r.bundle, tmp_path / "chat")
    b = read_bundle(hp)
    assert render_header(b) == hp.read_text(encoding="utf-8")
    loader = ContextLoader(b)
    assert loader.stats.expanded_tokens == 0              # nothing expanded up front
    res = loader.on_query("what is the deadline again?")
    assert [x.symbol for x in res] == ["deadline"]
    assert "November 15" in res[0].text and "November 22" in res[0].text   # history available
    assert loader.on_query("deadline?") == []             # resolved once per session


def test_loader_relinks_dropped_symbol_with_reason():
    loader = ContextLoader(compile_chat(*CHAT).bundle)
    res = loader.on_query("are push notifications still planned?")
    assert res and res[0].status == "dropped" and "R-SCOPE" in res[0].text


def test_header_marks_history_and_dropped_topics():
    h = compile_chat(*CHAT).header
    assert "deadline = November 22 (was November 15)" in h
    drop_line = next(l for l in h.splitlines() if l.startswith("DROPPED:"))
    assert "dark mode (cancelled)" in drop_line and "(abandoned)" in drop_line
    assert "5 minutes" not in h                              # the abandoned fact's value is not carried


# --- transcript formats -------------------------------------------------
def test_transcript_formats(tmp_path):
    assert [t.role for t in parse_text("User: hi\nAssistant: hello\nmore\nUser: bye")] == ["user", "assistant", "user"]
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"role": "user", "content": "a"}, {"role": "assistant",
                 "content": [{"type": "text", "text": "b"}]}]))
    assert [t.text for t in load_transcript(p)] == ["a", "b"]
    p.write_text(json.dumps({"chat_messages": [{"sender": "human", "text": "x"}]}))
    assert load_transcript(p)[0].role == "user"
    p.write_text(json.dumps({"current_node": "2", "mapping": {
        "1": {"message": {"author": {"role": "user"}, "content": {"parts": ["q"]}}, "parent": None},
        "2": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["r"]}}, "parent": "1"}}}))
    assert [t.text for t in load_transcript(p)] == ["q", "r"]


def test_compilation_is_deterministic():
    a = compile_file(SAMPLES / "campus_events_chat.txt").header
    b = compile_file(SAMPLES / "campus_events_chat.txt").header
    assert a == b


# --- local web server API ----------------------------------------------
def test_server_compile_sample_and_query():
    from ctxc.server import api_compile, api_query, api_reset
    data = api_compile({"sample": "campus_events"})
    assert data["gold"] and data["tokens"]["summary"] and data["session"]
    r = api_query({"session": data["session"], "query": "what is the deadline again?"})
    assert [x["symbol"] for x in r["resolved"]] == ["deadline"] and r["expanded_tokens"] > 0
    assert api_reset({"session": data["session"]})["expanded_tokens"] == 0


def test_server_compile_pasted_chat_without_summary_or_gold():
    from ctxc.server import api_compile
    data = api_compile({"transcript": "User: The deadline is March 3.\nUser: Change the deadline to March 10."})
    assert data["gold"] is None and data["tokens"]["summary"] is None
    assert "deadline = March 10 (was March 3)" in data["configs"]["strict"]["header"]


def test_server_rejects_empty_and_unknown():
    from ctxc.server import ApiError, api_compile, api_query
    with pytest.raises(ApiError):
        api_compile({"transcript": "   "})
    with pytest.raises(ApiError):
        api_query({"session": "nope", "query": "x"})


# --- follow-up question benchmark --------------------------------------
def test_probes_score_value_stale_and_status():
    from ctxc.probe import run_probes
    r = compile_chat(*CHAT)
    probes = [{"id": "d", "q": "When is the deadline?", "answer": ["November 22"], "stale": ["November 15"]},
              {"id": "n", "q": "Are push notifications still planned?", "not_current": "notification"},
              {"id": "x", "q": "What colour is the logo?", "answer": ["teal"]}]
    res = run_probes(r.bundle, "Deadline is November 15. Notifications were dropped.", probes)
    by = {row["id"]: row for row in res["rows"]}
    assert by["d"]["ir"] == "answerable" and by["d"]["summary"] == "wrong"     # summary only has the stale value
    assert by["n"]["ir"] == "answerable" and by["n"]["summary"] == "answerable"
    assert by["x"]["ir"] == "no info" and by["x"]["summary"] == "no info"
    assert res["totals"]["ir"]["answerable"] == 2
