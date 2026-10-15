# Evaluation: `fresh_hackathon_chat.txt`

Tokenizer: approx (word-piece heuristic).  Turns: 49.  Lexemes: 88.

## 1. Token cost of the handoff artifact

| Artifact | Tokens | % of transcript |
|---|---:|---:|
| Full transcript | 1760 | 100.0% |
| Prose summary (LLM-style compaction) | 344 | 19.5% |
| IR (strict, as proposed) header | 129 | 7.3% |
| IR (ablation: constraints global) header | 178 | 10.1% |

The IR bundle (full symbol table + audit log + original text, 9766 tokens) is *not* loaded up front; the loader reads from it only on demand.

## 2. Fact retention against the hand-written gold list

Gold list written before the pipeline was run; substring match, case-insensitive.

| Gold fact | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| event is called HackVIT | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| around 300 participants | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| teams of up to 4 | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| 24 hours starting Saturday morning | ✅ | ✅ carried (`event`, L-SCOPE) | ✅ carried (`event`, L-SCOPE) |
| venue is Anna Auditorium | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| date is 21 February | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| total budget 2 lakh rupees | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| food capped at 90,000 | ✅ | ❌ dropped `food_go.1` by **R-SCOPE** | ✅ carried (`food_go`, L-GLOBAL) |
| hacking ends 10 am Sunday | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| Devpost for submissions | ✅ | ❌ dropped `submissions.1` by **R-SCOPE** | ❌ dropped `submissions.1` by **R-SCOPE** |
| title sponsor is Zoho | ✅ | ✅ carried (`title_sponsor`, L-USE) | ✅ carried (`title_sponsor`, L-USE) |
| sponsors confirmed by 31 January | ✅ | ❌ dropped `sponsors_confirmed.1` by **R-SCOPE** | ✅ carried (`sponsors_confirmed`, L-GLOBAL) |
| prizes 30,000 / 20,000 / 10,000 | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| 4 judges | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| innovation, technical difficulty, presentation, equal weight | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |

**Expected-live facts carried:** summary: 15/15, IR (strict, as proposed): 2/15, IR (ablation: constraints global): 4/15

### Facts that should NOT be carried as current

| Item | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| (dropped) merch / hoodies / student designer (abandoned sub-topic) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (dropped) YouTube livestream (added, then retracted) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (stale) old date 14 February | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |

A prose summary that mentions a stale or abandoned item is not necessarily wrong (it may say "switched from X"), but the reader must parse prose to know which value is current; in the IR the stale version is simply not in the header and its SSA successor is.

## 3. Auditability

### 3a. Context IR — every eliminated symbol (IR (strict, as proposed))

| Symbol version | Value | Rule | Justification |
|---|---|---|---|
| `food_go.1` | Food can't go over 90,000 | R-SCOPE | declared in s1 'venue and date', closed at t24.1; never used afterwards; active chain = [s5, s0] |
| `submissions.1` | Devpost | R-SCOPE | declared in s3 'schedule', closed at t32.0; last use at t29.0 in inactive s3 'schedule'; active chain = [s5, s0] |
| `sponsors_confirmed.1` | Sponsors must be confirmed by 31 January so we can print banners | R-SCOPE | declared in s4 'sponsors', closed at t40.0; never used afterwards; active chain = [s5, s0] |

Extraction-stage discards (lexemes that never became symbols), by rule: E-ACK 1, E-EXPLORATORY 3, E-FILLER 17, E-NARRATIVE 52, E-PROPOSAL 1, E-QUESTION 4

Fate accounted for: **88 / 88 lexemes** (10 produced a symbol/scope event, 78 discarded with a named rule). Each is listed with its source location in the bundle's `audit` section.

### 3b. Prose summary — the equivalent list

The summary is free text. It contains no record of what it omitted, no link from any sentence to the turn it came from, and no rule explaining an omission. Concretely:

- Drop list in the artifact: **none** — it cannot be produced from the summary alone.
- Lexemes whose fate can be determined from the artifact: **0 / 88**.
- Summary sentences carrying a source pointer: **0**.
- Gold facts missing from the summary (found only by checking against the external gold list, not from the summary itself): none

## 5. Can a new session still answer follow-up questions?

Each question is asked to a fresh session that has only the handoff. Prose summary: the summary text. Context IR: the header plus what the loader fetches for that question. **Answerable** = the needed information is in that context; **wrong** = only an outdated value, or a cancelled item shown as current; **no info** = neither. This measures whether the information reaches the new session, not whether a model would then answer correctly.

| | Answerable | Wrong | No info | Tokens given to the new session |
|---|---:|---:|---:|---:|
| Prose summary | 17/17 | 0 | 0 | 344 |
| Context IR (header + loader) | 3/17 | 0 | 14 | 139 on average |
| IR header alone | 2/17 | 0 | 15 | 129 |

| Question | Prose summary | Context IR | Loader fetched |
|---|---|---|---|
| What is the hackathon called? | answerable | no info | — |
| How many participants do we expect? | answerable | no info | — |
| What is the maximum team size? | answerable | no info | — |
| How long does the hackathon run? | answerable | answerable | — |
| Where is the hackathon being held? | answerable | no info | — |
| What date is the hackathon? | answerable | no info | — |
| What is the total budget? | answerable | no info | — |
| How much can we spend on food? | answerable | no info | — |
| When does hacking end? | answerable | no info | — |
| How will teams submit their projects? | answerable | no info | — |
| Who is the title sponsor? | answerable | answerable | `title_sponsor` (live) |
| By when must sponsors be confirmed? | answerable | answerable | `sponsors_confirmed` (dropped) |
| How much is the first prize? | answerable | no info | — |
| How many judges will there be? | answerable | no info | — |
| What are the judging criteria? | answerable | no info | — |
| Are we making hoodies? | answerable | no info | — |
| Are we livestreaming the closing ceremony? | answerable | no info | — |

## 6. Context IR header (strict) as handed off

```
CTXIR/1 src=fresh_hackathon_chat.txt turns=49 | live=2 const=2 var=0 | dropped=3 (R-SCOPE 3)
Compiled conversation state. Values are current; ask to resolve(<name>) for original wording/history.
SCOPES (*active, x closed) +3 closed w/o live facts
*s0 conversation
  xs4 sponsors
  *s5 judging and prizes  <- current
CONST
K0 event = 24 hours long, starting saturday morning [fact s0]
K1 title_sponsor = Zoho [fact s4]
```
