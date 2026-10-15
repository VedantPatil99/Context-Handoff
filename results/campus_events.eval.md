# Evaluation: `campus_events_chat.txt`

Tokenizer: approx (word-piece heuristic).  Turns: 69.  Lexemes: 161.

## 1. Token cost of the handoff artifact

| Artifact | Tokens | % of transcript |
|---|---:|---:|
| Full transcript | 3497 | 100.0% |
| Prose summary (LLM-style compaction) | 427 | 12.2% |
| IR (strict, as proposed) header | 284 | 8.1% |
| IR (ablation: constraints global) header | 378 | 10.8% |

The IR bundle (full symbol table + audit log + original text, 20829 tokens) is *not* loaded up front; the loader reads from it only on demand.

## 2. Fact retention against the hand-written gold list

Gold list written before the pipeline was run; substring match, case-insensitive.

| Gold fact | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| students browse, club organisers post | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| two-person team incl. Dhanjith | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| deadline is November 22 | ✅ | ✅ carried (`deadline`, L-SCOPE) | ✅ carried (`deadline`, L-SCOPE) |
| app is called CampusPulse | ✅ | ✅ carried (`app_name`, L-SCOPE) | ✅ carried (`app_name`, L-SCOPE) |
| must run on college server, no cloud | ✅ | ✅ carried (`everything_run`, L-PIN) | ✅ carried (`everything_run`, L-PIN) |
| students browse without an account | ✅ | ❌ dropped `students_need.1` by **R-SCOPE** | ✅ carried (`students_need`, L-GLOBAL) |
| categories: tech, cultural, sports, academic | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| backend is Express | ✅ | ✅ carried (`backend`, L-USE) | ✅ carried (`backend`, L-USE) |
| database is PostgreSQL | ✅ | ✅ carried (`database`, L-USE) | ✅ carried (`database`, L-USE) |
| frontend is React with Vite | ✅ | ✅ carried (`frontend`, L-USE) | ✅ carried (`frontend`, L-USE) |
| one image per event, max 2 MB | ✅ | ❌ dropped `image_event_max.1` by **R-SCOPE** | ✅ carried (`image_event_max`, L-GLOBAL) |
| Knex for migrations | ✅ | ✅ carried (`migrations`, L-USE) | ✅ carried (`migrations`, L-USE) |
| session cookies for organiser auth | ✅ | ✅ carried (`auth`, L-USE) | ✅ carried (`auth`, L-USE) |
| every API response must be JSON | ✅ | ❌ dropped `every_api_response_json.1` by **R-SCOPE** | ✅ carried (`every_api_response_json`, L-GLOBAL) |
| offset pagination, 20 per page | ✅ | ❌ dropped `api_design_choice.1` by **R-SCOPE** | ❌ dropped `api_design_choice.1` by **R-SCOPE** |
| Jest + Supertest for backend tests | ✅ | ✅ carried (`backend_tests`, L-SCOPE) | ✅ carried (`backend_tests`, L-SCOPE) |
| backend coverage >= 70% | ✅ | ✅ carried (`test_coverage_least`, L-SCOPE) | ✅ carried (`test_coverage_least`, L-GLOBAL) |
| app runs on port 3000 | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |

**Expected-live facts carried:** summary: 18/18, IR (strict, as proposed): 10/18, IR (ablation: constraints global): 13/18

### Facts that should NOT be carried as current

| Item | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| (dropped) notifications within 5 minutes (abandoned sub-topic) | absent | absent — dropped `notifications_sent.1` by **R-SCOPE** | mentioned — carried (`notifications_sent`, L-GLOBAL) |
| (dropped) dark mode (decided, then retracted) | mentioned | absent — dropped `add_dark_mode.1` by **R-KILL** | absent — dropped `add_dark_mode.1` by **R-KILL** |
| (dropped) RSVP (exploratory, never decided) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (stale) old deadline November 15 | mentioned | absent — dropped `deadline.1` by **R-SSA** | absent — dropped `deadline.1` by **R-SSA** |
| (stale) old name EventHub | mentioned | absent — dropped `app_name.1` by **R-SSA** | absent — dropped `app_name.1` by **R-SSA** |
| (stale) old database MongoDB | mentioned | absent — dropped `database.1` by **R-SSA** | absent — dropped `database.1` by **R-SSA** |

A prose summary that mentions a stale or abandoned item is not necessarily wrong (it may say "switched from X"), but the reader must parse prose to know which value is current; in the IR the stale version is simply not in the header and its SSA successor is.

## 3. Auditability

### 3a. Context IR — every eliminated symbol (IR (strict, as proposed))

| Symbol version | Value | Rule | Justification |
|---|---|---|---|
| `deadline.1` | November 15 | R-SSA | dead store: superseded by deadline.2 |
| `app_name.1` | EventHub | R-SSA | dead store: superseded by app_name.2 |
| `students_need.1` | Students should not need an account to browse | R-SCOPE | declared in s1 'requirements', closed at t16.0; never used afterwards; active chain = [s7, s6, s0] |
| `database.1` | MongoDB | R-SSA | dead store: superseded by database.2 |
| `notifications_sent.1` | Notifications should be sent within 5 minutes of an event being posted | R-SCOPE | declared in s3 'hosting the mobile push notifications part', closed at t28.1; never used afterwards; active chain = [s7, s6, s0] |
| `image_event_max.1` | One image per event, max 2 MB | R-SCOPE | declared in s4 'data model', closed at t40.0; never used afterwards; active chain = [s7, s6, s0] |
| `add_dark_mode.1` | add dark mode then | R-KILL | explicitly retracted at t54.0 |
| `every_api_response_json.1` | Every API response must be JSON, even errors | R-SCOPE | declared in s5 'API design', closed at t56.0; never used afterwards; active chain = [s7, s6, s0] |
| `api_design_choice.1` | offset pagination with 20 events per page | R-SCOPE | declared in s5 'API design', closed at t56.0; never used afterwards; active chain = [s7, s6, s0] |

Extraction-stage discards (lexemes that never became symbols), by rule: E-ACK 6, E-CODE 2, E-EXPLORATORY 4, E-FILLER 13, E-NARRATIVE 97, E-PROPOSAL 1, E-QUESTION 10

Fate accounted for: **161 / 161 lexemes** (28 produced a symbol/scope event, 133 discarded with a named rule). Each is listed with its source location in the bundle's `audit` section.

### 3b. Prose summary — the equivalent list

The summary is free text. It contains no record of what it omitted, no link from any sentence to the turn it came from, and no rule explaining an omission. Concretely:

- Drop list in the artifact: **none** — it cannot be produced from the summary alone.
- Lexemes whose fate can be determined from the artifact: **0 / 161**.
- Summary sentences carrying a source pointer: **0**.
- Gold facts missing from the summary (found only by checking against the external gold list, not from the summary itself): none

## 4. Loader: on-demand resolution

Header loaded up front: 284 tokens.

| Follow-up query | Symbols resolved (matched by → status) | Tokens added |
|---|---|---:|
| Ok. What's left for us to do this week for the PM2 piece? | — (none referenced) | 0 |
| What page size did we pick for pagination? | `api_design_choice` (keyword → dropped) | 100 |
| Remind me what the deadline is again. | `deadline` (name → live) | 136 |
| Are we still doing push notifications? | `notifications_sent` (keyword → dropped) | 120 |
| Should the error responses be HTML or JSON? | — (none referenced) | 0 |

Total after all queries: header 284 + expansions 356 = 640 tokens (vs. 3497 for re-reading the transcript).

## 5. Can a new session still answer follow-up questions?

Each question is asked to a fresh session that has only the handoff. Prose summary: the summary text. Context IR: the header plus what the loader fetches for that question. **Answerable** = the needed information is in that context; **wrong** = only an outdated value, or a cancelled item shown as current; **no info** = neither. This measures whether the information reaches the new session, not whether a model would then answer correctly.

| | Answerable | Wrong | No info | Tokens given to the new session |
|---|---:|---:|---:|---:|
| Prose summary | 21/21 | 0 | 0 | 427 |
| Context IR (header + loader) | 14/21 | 0 | 7 | 376 on average |
| IR header alone | 10/21 | 0 | 11 | 284 |

| Question | Prose summary | Context IR | Loader fetched |
|---|---|---|---|
| Who posts events and who just browses them? | answerable | no info | `api_design_choice` (dropped) |
| Who is on the team with me? | answerable | no info | — |
| When is the project deadline? | answerable | answerable | `deadline` (live) |
| What is the app called? | answerable | answerable | — |
| Where does the app have to be hosted? | answerable | answerable | — |
| Do students need an account to browse events? | answerable | answerable | `students_need` (dropped), `api_design_choice` (dropped) |
| What event categories do we support? | answerable | no info | `notifications_sent` (dropped), `image_event_max` (dropped) |
| What backend framework are we using? | answerable | answerable | `backend` (live) |
| Which database are we using? | answerable | answerable | `database` (live) |
| What is the frontend built with? | answerable | answerable | `frontend` (live) |
| What is the size limit for event images? | answerable | answerable | `notifications_sent` (dropped), `image_event_max` (dropped) |
| What tool handles database migrations? | answerable | answerable | `database` (live), `migrations` (live) |
| How do organisers stay logged in? | answerable | answerable | — |
| What format should API error responses use? | answerable | no info | — |
| How many events are shown per page? | answerable | answerable | `api_design_choice` (dropped) |
| What do we use for backend tests? | answerable | answerable | `backend` (live), `backend_tests` (live) |
| What backend test coverage do we need? | answerable | answerable | `backend` (live), `test_coverage_least` (live) |
| Which port does the app run on? | answerable | no info | — |
| Are push notifications still part of the plan? | answerable | answerable | `notifications_sent` (dropped) |
| Is dark mode still in the plan? | answerable | no info | — |
| Are we building an RSVP feature? | answerable | no info | — |

## 6. Context IR header (strict) as handed off

```
CTXIR/1 src=campus_events_chat.txt turns=69 | live=10 const=7 var=3 | dropped=9 (R-KILL 1, R-SCOPE 5, R-SSA 3)
Compiled conversation state. Values are current; ask to resolve(<name>) for original wording/history.
SCOPES (*active, x closed) +2 closed w/o live facts
*s0 conversation
  xs2 tech stack
    xs4 data model
    xs5 API design
  *s6 testing and deployment
    *s7 PM2 setup on the server  <- current
CONST
K0 everything_run = Everything has to run on the college server, we don't get cloud credits [con s0 pin]
K1 backend = Express [cho s2]
K2 frontend = React with Vite [cho s2]
K3 migrations = Knex [cho s4]
K4 auth = session cookies [cho s5]
K5 backend_tests = Jest and Supertest [cho s6]
K6 test_coverage_least = Test coverage must be at least 70% for the backend [con s6]
VAR
deadline.2 = November 22 [fact s5, 1 prior]
app_name.2 = CampusPulse [fact s5, 1 prior]
database.2 = PostgreSQL [cho s4, 1 prior]
```
