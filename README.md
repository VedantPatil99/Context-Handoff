# ctxc — An Auditable Context Handoff Mechanism Built on Compiler IR Concepts

Implementation for the BCSE307P Compiler Design Lab project (Vedant Patil 23BCE0816, Dhanjith Velluva 23BCE0763).
A long LLM conversation is **compiled** into a compact *Context IR* instead of being paraphrased into a prose
summary, and that IR is compared against an LLM-style prose compaction.

Pure Python 3.10+, standard library only (optional `tiktoken` for exact token counts). No LLM or API is called anywhere.

## Update: v2 (improved compiler) and the follow-up question benchmark

`ctxc/` is now **v2**. The original design from the proposal is frozen, unchanged, in `ctxc_v1/` (`python -m ctxc_v1 ...`).

**Question benchmark** (`ctxc/probe.py`, `samples/*_probes.json`): every question goes to a fresh session that has only the handoff. For the IR that means the header plus whatever the loader fetches for that question. A question counts as answerable when the needed information is in that context. This does not test whether a model would then answer correctly.

| | Prose summary | IR v1 (as proposed) | IR v2 |
|---|---|---|---|
| Campus chat (development) | 21/21 · 427 tokens | 14/21 · 284 | 20/21 · 267 |
| ML report chat (development) | 14/14 · 388 | 6/14 · 183 | 14/14 · 263 |
| **Hackathon chat (fresh, held out)** | **17/17 · 344** | **3/17 · 129** | **15/17 · 214** |

The fresh hackathon chat was written before any v2 code existed. Its files are locked by `samples/fresh_hackathon.LOCKED.sha256`, and the v1 baseline was saved unopened (`results/fresh_hackathon_V1_BASELINE.eval.md`). The development numbers are inflated because the rules were fixed while looking at those chats. On the fresh chat, v2 gives a handoff 62% the size of the summary and answers 15 of 17 questions against the summary's 17. Neither method gave a wrong answer (an outdated value, or a cancelled item shown as current) on any chat.

**What changed in v2:**

- **Extraction:** user statements carrying a concrete value are kept; "key: a, b, c" lists are read; corrections ("actually it's 0.47") become new SSA versions; rejected items ("skip RSVP") become cancelled tombstones.
- **Reachability, commit vs rollback:** a phase that ends normally exports its facts (`L-EXPORT`), like a function returning its results. Only a phase that is abandoned ("too complicated, come back later") drops them (`R-SCOPE`), like unwinding on an exception.
- **Header format 2:** no per-line tags; previous values are shown inline ("November 22 (was November 15)"); one `DROPPED:` line lists cancelled and abandoned topics.
- **Loader:** fetches only dropped facts, or a live fact's history when the question asks for it; matches stems and shorter words.

**Remaining misses:** descriptive statements with no concrete value ("Students browse, club organisers post", "criteria will be innovation, technical difficulty and presentation"), and abandoned sub-steps that were never recognised as a phase.

**Not yet updated:** the reports in `results/` and both dashboards still show v1 numbers.


## Pipeline ↔ compiler concepts

| Proposal module (§3.2) | File | Compiler concept used |
|---|---|---|
| (source reader) | `ctxc/transcript.py` | Reads plain text, `{role, content}` JSON, chat-app data exports (e.g. ChatGPT), JSONL |
| (lexical analysis) | `ctxc/lexer.py` | Sentence-level lexemes, each with a source location `t<turn>.<seq>` |
| Fact / Symbol Extraction | `ctxc/extractor.py` | One-pass front end: a regex *grammar* classifies each lexeme as a definition, revision, retraction, scope directive or discard (question, exploratory, acknowledgement, filler, code) |
| Symbol Table Builder | `ctxc/symtab.py` | Symbol table with **SSA versioning** (`deadline.1` → `deadline.2`, old version marked `killed_by`), restatements folded into *uses*, **lexical name resolution** (innermost scope first) |
| Scope Stack Builder | `ctxc/scopes.py` | Explicit **scope stack** / scope tree: block entry (nested sub-task), leave-and-enter (phase transition), block exit (done), unwind-and-reenter (back to X) |
| Reachability Analysis | `ctxc/reachability.py` | **Mark-and-sweep dead-symbol elimination**: root set = active scope chain, pinned symbols, uses from active scopes; worklist propagation through value references; every sweep logged with a rule |
| Constant Pool Separator | `ctxc/constpool.py` | **Constant pool**: live single-assignment symbols hoisted out of scopes and interned by value |
| Context IR Serializer | `ctxc/ir.py` | "Object file": compact header `.ctxir` (the handoff) + bundle `.ctxir.json` (symbol table, audit log, original text) |
| Loader / New Session | `ctxc/loader.py` | **Lazy linking**: only the header is loaded; a symbol is expanded from the bundle only when a new query references it. Dropped symbols are *relinked* on demand, along with the rule that dropped them |
| Evaluation | `ctxc/evaluate.py` | Token cost, retention against a gold list, audit lists, loader cost |

### Reachability rules (what the audit log cites)

| Rule | Meaning |
|---|---|
| `L-SCOPE` | declared in (or assigned in) a scope on the active chain (innermost task → root) |
| `L-USE` | used, by name or value, from an active scope after its current definition (it escaped its scope) |
| `L-REF` | the value of a live symbol mentions it (transitive) |
| `L-PIN` | the user pinned it ("Important: …", "Remember that …") |
| `R-SSA` | dead store: superseded by a later version |
| `R-KILL` | explicitly retracted ("forget about X") |
| `R-SCOPE` | declared in a scope no longer on the active chain and never used from it |
| `L-GLOBAL` | **ablation only** (`--global-constraints`): constraints treated as global declarations. Not part of the proposal |

## Usage

```bash
python -m ctxc compile samples/campus_events_chat.txt -o results/campus_events
```
```bash
python -m ctxc audit results/campus_events.ctxir.json
```
```bash
python -m ctxc query results/campus_events.ctxir.json "Remind me what the deadline is again."
```
```bash
python -m ctxc session results/campus_events.ctxir.json
```
```bash
python -m ctxc eval samples/campus_events_chat.txt --summary samples/campus_events_summary.txt --gold samples/campus_events_gold.json --queries samples/campus_events_queries.txt -o results/campus_events.eval.md
```
```bash
python -m pytest -q tests
```

### Web app (local server)

```bash
python -m ctxc serve
```

Then open http://localhost:8000. The server uses Python's built-in `http.server` (nothing to install) and listens on this machine only. Every chat on the page is compiled live by the pipeline:

- pick one of the samples, or paste or upload your own chat, with an optional prose summary to compare against;
- the page shows the result, the four compiler passes on that chat, both handoffs side by side, and what was dropped and why;
- "Ask a follow-up" sends your messages to the real `ContextLoader`, which fetches only the facts you mention.

API (`ctxc/server.py`): `GET /api/samples`, `POST /api/compile`, `POST /api/query`, `POST /api/reset`.

### Static dashboard (offline snapshot)

```bash
python -m ctxc dashboard
```

This compiles both samples and writes `results/dashboard.html` on your machine (generated pages are not committed to the repository). It is the same page with the results saved into it, so it works offline, built from the pipeline's own output, with no numbers typed in by hand. It reads top to bottom:

1. The result.
2. The four compiler passes, each shown on a real example from the chat.
3. Both handoffs side by side.
4. A fact checklist.
5. What each method can tell you about what it dropped.

`python -m ctxc dashboard --detailed` writes `results/dashboard_detailed.html`, the full explorer with these tabs:

- **Step through:** replays the front end sentence by sentence, showing the scope stack and symbol table after each sentence, the rule that fired, and each value's final verdict.
- **Symbol table:** every symbol with its SSA version chain and the reachability verdict.
- **Scopes:** the scope tree, the active chain, and every scope open/close event.
- **IR vs prose summary:** token bars, fact retention against the gold list, both artifacts side by side, the auditability table, and the full drop list.
- **Loader:** type a follow-up message and see which symbols get expanded, and what that costs in tokens.

The top bar switches between the two transcripts and between the strict reachability rule and the "constraints global" variant. The page template is `ctxc/dashboard_template.html`.

## Evaluation setup

- **Transcripts** (`samples/`): both are *synthetic* conversations written by an AI assistant, not real logs.
  - `campus_events_chat.txt` is a 69-turn planning chat. It contains three facts that change value (deadline, app name, database), one abandoned sub-topic (push notifications) and one retraction (dark mode).
  - `heldout_ml_report_chat.txt` is a 39-turn chat on a different topic. It was written *after* the extractor had been fixed on the first sample, to measure how well the rules generalise.
- **Prose baseline** (`*_summary.txt`): written by an AI assistant, the way it would normally compact a conversation, as the project team asked, so no API was used. This is a high-quality summary. It is also not independent, since the same model wrote the IR code and the gold lists.
- **Gold lists** (`*_gold.json`): hand-written before each first run and never edited afterwards. A fact counts as carried if any of its match strings appears in the artifact.
- **Token counts**: `tiktoken` was not installed, so all counts use an approximate word-piece counter. Every artifact is counted with the same counter, so the ratios are comparable but the absolute numbers are not exact BPE counts.

## Results

Full reports: `results/campus_events.eval.md`, `results/heldout_ml_report.eval.md`, `results/heldout_FIRST_RUN.eval.md`.

### Token cost (tokens, % of transcript)

| | Campus events (69 turns) | Held-out ML report (39 turns) |
|---|---|---|
| Full transcript | 3497 (100%) | 1419 (100%) |
| Prose summary | 427 (12.2%) | 388 (27.3%) |
| **IR header, strict (as proposed)** | **284 (8.1%)** | **183 (12.9%)** |
| IR header, ablation (constraints global) | 378 (10.8%) | 183 (12.9%) |

### Expected-live facts carried in the handoff

| | Campus events | Held-out: first run | Held-out: after the informal-text fix |
|---|---|---|---|
| Prose summary | **18/18** | **12/12** | **12/12** |
| IR strict | 10/18 | 5/12 (one carried value was **stale**) | 4/12 |
| IR ablation | 13/18 | 5/12 | 4/12 |

### Auditability

| | Context IR | Prose summary |
|---|---|---|
| List of dropped facts with the rule that dropped each | Yes (see e.g. §3a of each report) | **No such list exists in the artifact** |
| Lexemes whose fate (symbol / named discard rule) is recorded | 161/161 and 66/66 | 0 |
| Sentences carrying a source pointer | every symbol version has a `t<turn>.<seq>` location | 0 |
| Stale values distinguishable from current values | structurally (`deadline.2` current, `deadline.1` dropped by `R-SSA`) | only by reading the prose ("moved from November 15 to November 22") |
| Dropped item recoverable later | yes: the loader relinks it from the bundle and states the rule | no |

### Loader

Campus: the header (284 tokens) plus on-demand expansions for 5 follow-up queries came to **640 tokens**, against 3497 for re-reading the transcript.
Two of the expansions were symbols that `R-SCOPE` had dropped (pagination, notifications). They were relinked with their drop reason, so some of the retention losses above can be recovered *if* a later query mentions the fact.

## Honest assessment: where this breaks down

**Bottom line:** the IR is smaller than the prose summary and genuinely auditable, but on these samples it carries **far fewer of the facts that matter**. The proposal's claim of being "materially smaller than … a prose summary *of equivalent fidelity*" is **not supported** by these results, because the IR does not reach equivalent fidelity.

1. **Extraction is the bottleneck.** The front end catches facts phrased as choices, assignments or constraints. It misses facts stated descriptively:
   - "Students browse, club organisers post."
   - "It's me and one teammate, Dhanjith."
   - "The app will run on port 3000."
   - "XGBoost got RMSE of 0.47 t/ha"
   - "we trained linear regression, random forest and XGBoost"

   It also misses facts given as answers to the assistant's questions, and lists ("support event categories: tech, cultural, …"). The prose summary, written by an LLM, has none of these problems.

2. **Task scope is a poor proxy for relevance (a flaw in the design, not just the code).** `R-SCOPE` correctly dropped the abandoned notifications sub-topic. But it also dropped facts that are still needed:
   - "every API response must be JSON"
   - "students don't need accounts"
   - "max 2 MB images"
   - "offset pagination, 20/page"
   - in the held-out chat, the evaluation metric and the train/test split

   All were dropped only because the phase they were stated in had closed. The ablation that treats constraints as global recovers 3 of these on the campus chat, but it then also keeps the *abandoned* notifications constraint. There's a real trade-off between keeping too much and dropping too much, and the scope structure alone can't resolve it.

3. **Liveness through `L-USE` is partly luck.** Express, Knex and PostgreSQL stayed live mainly because the *assistant* happened to mention them again during the deployment phase.

4. **Front-end misses can make the IR confidently wrong.** In the held-out first run, the sentence carrying the split change ("good point. change the split …") was not split off or parsed. The IR therefore presented the **stale "80/20 random split" as current**. A prose summary that leaves a fact out is less dangerous than an IR that states a superseded value with SSA-style confidence.

5. **The scope builder is ambiguous.** "Let's work on X" is taken as a sibling at the current depth, so whether X is a sub-step or a new top-level phase depends on wording ("Moving on to …" pops to top level). In the campus chat, "data model" and "API design" ended up nested under "tech stack".

6. **Generalisation is weak and the rules were tuned on sample 1.** Section "Changes after the first run" lists every rule change made after seeing results. The held-out first run (5/12) is the honest measure of generalisation. Post-fix numbers on either sample are not held out.

7. **The loader matches by surface form.** It misses paraphrases: "error responses … JSON" did not resolve `every_api_response_json`, because "JSON" is too short to count as a distinctive keyword. It also produces false positives: "are we including the SHAP plot?" resolved `pages_max_including` through the word "including".

8. **Symbol names are heuristic** (`everything_run`, `api_design_choice`, `test_coverage_least`). They're readable but not ideal names.

9. **The evaluation is small and not independent.** There are two synthetic transcripts, substring-matched gold lists, and a baseline written by the same model that wrote the code. The numbers are indicative only.

**What does hold up:** SSA versioning works as intended: every changed fact that was extracted shows its current value, with the old value dropped by `R-SSA` and its history kept. Explicit retraction (`R-KILL`) and abandoned-sub-topic elimination (`R-SCOPE`) work. The audit trail is complete: every sentence's fate is recorded and every drop is justified with a source location. Lazy loading keeps follow-up cost low. The prose summary cannot provide any of this.

**Natural next steps:**
- Replace or augment the regex front end with an LLM extractor that emits the *same* explicit event schema (define / revise / retract / scope), so the compiler passes and audit trail stay unchanged.
- Treat project-wide constraints and requirements as global declarations by default.
- Use use-based liveness (whether a fact is referenced again) more than closure of the phase it was stated in.

## Changes after the first run (disclosed)

**On the campus sample**, after its first run, all of these generic changes:
- **SSA ownership:** a symbol is owned by its *declaring* scope, so assigning it from an inner scope no longer moves it.
- **Normalisation:** strip leading discourse markers ("And", "Yes,", "Actually,") and trailing "since/because" clauses.
- **Scope names:** cut at clause boundaries.
- **Keys:** reject keys containing adverbs; derive keys correctly from phrases starting with "one".
- **Constraint rule:** accept plain "should"; keyword constraints now need a number.
- **Grammar:** support the fronted form "For X, we'll use Y".

**On the held-out sample, after its first run** (preserved in `results/heldout_FIRST_RUN.eval.md`):
- accept lowercase sentence starts;
- restore dropped apostrophes ("lets", "dont").

**Found by the unit tests:** the acknowledgement rule swallowed sentences like "Ok we decided to add dark mode." It is now limited to the ack word plus at most three words.

## Layout

```
ctxc/        compiler passes, loader, evaluation, CLI
samples/     transcripts, gold lists, prose summaries, follow-up queries
results/     evaluation reports (.md/.json) and compiled IR (.ctxir / .ctxir.json)
tests/       pytest suite (15 tests on pass behaviour, not on scores)
```
