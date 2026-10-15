# Evaluation: `heldout_ml_report_chat.txt`

Tokenizer: approx (word-piece heuristic).  Turns: 39.  Lexemes: 66.

## 1. Token cost of the handoff artifact

| Artifact | Tokens | % of transcript |
|---|---:|---:|
| Full transcript | 1419 | 100.0% |
| Prose summary (LLM-style compaction) | 388 | 27.3% |
| IR (strict, as proposed) header | 183 | 12.9% |
| IR (ablation: constraints global) header | 183 | 12.9% |

The IR bundle (full symbol table + audit log + original text, 8157 tokens) is *not* loaded up front; the loader reads from it only on demand.

## 2. Fact retention against the hand-written gold list

Gold list written before the pipeline was run; substring match, case-insensitive.

| Gold fact | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| crop yield prediction from weather data | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| FAO + NASA POWER, ~28,000 rows | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| IEEE conference format | ✅ | ✅ carried (`report_ieee`, L-SCOPE) | ✅ carried (`report_ieee`, L-GLOBAL) |
| 6 pages max incl. references | ✅ | ✅ carried (`pages_max_including`, L-SCOPE) | ✅ carried (`pages_max_including`, L-GLOBAL) |
| due December 5 | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| linear regression, random forest, XGBoost | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| RMSE primary metric | ✅ | ❌ dropped `evaluation_metric.1` by **R-SCOPE** | ❌ dropped `evaluation_metric.1` by **R-SCOPE** |
| R2 reported as well | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| temporal split 1990-2015 / 2016-2020 | ✅ | ❌ dropped `train_test_split.2` by **R-SCOPE** | ❌ dropped `train_test_split.2` by **R-SCOPE** |
| XGBoost test RMSE 0.47 t/ha | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| at least 15 references | ✅ | ✅ carried (`least_15_references`, L-SCOPE) | ✅ carried (`least_15_references`, L-GLOBAL) |
| Zotero for references | ✅ | ✅ carried (`related_work_section_choice`, L-SCOPE) | ✅ carried (`related_work_section_choice`, L-SCOPE) |

**Expected-live facts carried:** summary: 12/12, IR (strict, as proposed): 4/12, IR (ablation: constraints global): 4/12

### Facts that should NOT be carried as current

| Item | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| (dropped) grid search / 5-fold CV detail (abandoned sub-topic) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (dropped) SHAP plot (added then dropped) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (stale) old 80/20 random split | mentioned | absent — dropped `train_test_split.1` by **R-SSA** | absent — dropped `train_test_split.1` by **R-SSA** |
| (stale) old RMSE 0.42 | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |

A prose summary that mentions a stale or abandoned item is not necessarily wrong (it may say "switched from X"), but the reader must parse prose to know which value is current; in the IR the stale version is simply not in the header and its SSA successor is.

## 3. Auditability

### 3a. Context IR — every eliminated symbol (IR (strict, as proposed))

| Symbol version | Value | Rule | Justification |
|---|---|---|---|
| `evaluation_metric.1` | RMSE | R-SCOPE | declared in s1 'methodology section', closed at t32.0; last use at t28.0 in inactive s1 'methodology section'; active chain = [s2, s0] |
| `train_test_split.1` | an 80/20 random split | R-SSA | dead store: superseded by train_test_split.2 |
| `train_test_split.2` | a temporal split, train on 1990-2015 and test on 2016-2020 | R-SCOPE | declared in s1 'methodology section', closed at t32.0; never used afterwards; active chain = [s2, s0] |

Extraction-stage discards (lexemes that never became symbols), by rule: E-ACK 1, E-EXPLORATORY 2, E-FILLER 14, E-NARRATIVE 35, E-PROPOSAL 1, E-QUESTION 4

Fate accounted for: **66 / 66 lexemes** (9 produced a symbol/scope event, 57 discarded with a named rule). Each is listed with its source location in the bundle's `audit` section.

### 3b. Prose summary — the equivalent list

The summary is free text. It contains no record of what it omitted, no link from any sentence to the turn it came from, and no rule explaining an omission. Concretely:

- Drop list in the artifact: **none** — it cannot be produced from the summary alone.
- Lexemes whose fate can be determined from the artifact: **0 / 66**.
- Summary sentences carrying a source pointer: **0**.
- Gold facts missing from the summary (found only by checking against the external gold list, not from the summary itself): none

## 4. Loader: on-demand resolution

Header loaded up front: 183 tokens.

| Follow-up query | Symbols resolved (matched by → status) | Tokens added |
|---|---|---:|
| ok, what should the first paragraph of related work say? | `related_work_section_choice` (keyword → live) | 79 |
| what RMSE did XGBoost get? | `evaluation_metric` (value → dropped) | 98 |
| how are we splitting train and test? | `train_test_split` (keyword → dropped) | 174 |
| are we including the SHAP plot? | `pages_max_including` (keyword → live) | 88 |

Total after all queries: header 183 + expansions 439 = 622 tokens (vs. 1419 for re-reading the transcript).

## 5. Can a new session still answer follow-up questions?

Each question is asked to a fresh session that has only the handoff. Prose summary: the summary text. Context IR: the header plus what the loader fetches for that question. **Answerable** = the needed information is in that context; **wrong** = only an outdated value, or a cancelled item shown as current; **no info** = neither. This measures whether the information reaches the new session, not whether a model would then answer correctly.

| | Answerable | Wrong | No info | Tokens given to the new session |
|---|---:|---:|---:|---:|
| Prose summary | 14/14 | 0 | 0 | 388 |
| Context IR (header + loader) | 6/14 | 0 | 8 | 255 on average |
| IR header alone | 4/14 | 0 | 10 | 183 |

| Question | Prose summary | Context IR | Loader fetched |
|---|---|---|---|
| What is the project about? | answerable | no info | — |
| Which datasets are we using? | answerable | no info | — |
| What format does the report have to follow? | answerable | answerable | `report_ieee` (live) |
| What is the page limit? | answerable | answerable | — |
| When is the report due? | answerable | no info | `report_ieee` (live) |
| Which models did we train? | answerable | no info | `train_test_split` (dropped) |
| What is the main evaluation metric? | answerable | answerable | `evaluation_metric` (dropped) |
| Are we reporting any secondary metric? | answerable | no info | `evaluation_metric` (dropped) |
| How are we splitting train and test data? | answerable | answerable | `train_test_split` (dropped) |
| What test RMSE did XGBoost get? | answerable | no info | `evaluation_metric` (dropped) |
| How many references do we need? | answerable | answerable | — |
| What are we using to manage references? | answerable | answerable | `related_work_section_choice` (live) |
| Are we describing the grid search in detail? | answerable | no info | — |
| Are we including the SHAP plot? | answerable | no info | `pages_max_including` (live) |

## 6. Context IR header (strict) as handed off

```
CTXIR/1 src=heldout_ml_report_chat.txt turns=39 | live=4 const=4 var=0 | dropped=3 (R-SCOPE 2, R-SSA 1)
Compiled conversation state. Values are current; ask to resolve(<name>) for original wording/history.
SCOPES (*active, x closed) +1 closed w/o live facts
*s0 conversation
  *s2 related work section  <- current
CONST
K0 report_ieee = Report has to be in IEEE conference format [con s0]
K1 pages_max_including = 6 pages max including references [con s0]
K2 least_15_references = We need at least 15 references [con s2]
K3 related_work_section_choice = Zotero to manage the references [cho s2]
```
