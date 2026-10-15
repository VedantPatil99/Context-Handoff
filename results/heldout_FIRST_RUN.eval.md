# Evaluation: `heldout_ml_report_chat.txt`

Tokenizer: approx (word-piece heuristic).  Turns: 39.  Lexemes: 62.

## 1. Token cost of the handoff artifact

| Artifact | Tokens | % of transcript |
|---|---:|---:|
| Full transcript | 1419 | 100.0% |
| Prose summary (LLM-style compaction) | 388 | 27.3% |
| IR (strict, as proposed) header | 174 | 12.3% |
| IR (ablation: constraints global) header | 174 | 12.3% |

The IR bundle (full symbol table + audit log + original text, 7153 tokens) is *not* loaded up front; the loader reads from it only on demand.

## 2. Fact retention against the hand-written gold list

Gold list written before the pipeline was run; substring match, case-insensitive.

| Gold fact | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| crop yield prediction from weather data | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| FAO + NASA POWER, ~28,000 rows | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| IEEE conference format | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| 6 pages max incl. references | ✅ | ✅ carried (`pages_max_including`, L-SCOPE) | ✅ carried (`pages_max_including`, L-GLOBAL) |
| due December 5 | ✅ | ✅ carried (`pages_max_including`, L-SCOPE) | ✅ carried (`pages_max_including`, L-GLOBAL) |
| linear regression, random forest, XGBoost | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| RMSE primary metric | ✅ | ✅ carried (`evaluation_metric`, L-SCOPE) | ✅ carried (`evaluation_metric`, L-SCOPE) |
| R2 reported as well | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| temporal split 1990-2015 / 2016-2020 | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| XGBoost test RMSE 0.47 t/ha | ✅ | ❌ **not extracted** (front-end miss) | ❌ **not extracted** (front-end miss) |
| at least 15 references | ✅ | ✅ carried (`least_15_references`, L-SCOPE) | ✅ carried (`least_15_references`, L-GLOBAL) |
| Zotero for references | ✅ | ✅ carried (`zotero_manage_choice`, L-SCOPE) | ✅ carried (`zotero_manage_choice`, L-SCOPE) |

**Expected-live facts carried:** summary: 12/12, IR (strict, as proposed): 5/12, IR (ablation: constraints global): 5/12

### Facts that should NOT be carried as current

| Item | Prose summary | IR (strict, as proposed) | IR (ablation: constraints global) |
|---|---|---|---|
| (dropped) grid search / 5-fold CV detail (abandoned sub-topic) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (dropped) SHAP plot (added then dropped) | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |
| (stale) old 80/20 random split | mentioned | mentioned — carried (`train_test_split`, L-SCOPE) | mentioned — carried (`train_test_split`, L-SCOPE) |
| (stale) old RMSE 0.42 | mentioned | absent — **not extracted** (front-end miss) | absent — **not extracted** (front-end miss) |

A prose summary that mentions a stale or abandoned item is not necessarily wrong (it may say "switched from X"), but the reader must parse prose to know which value is current; in the IR the stale version is simply not in the header and its SSA successor is.

## 3. Auditability

### 3a. Context IR — every eliminated symbol (IR (strict, as proposed))

| Symbol version | Value | Rule | Justification |
|---|---|---|---|

Extraction-stage discards (lexemes that never became symbols), by rule: E-ACK 1, E-EXPLORATORY 2, E-FILLER 14, E-NARRATIVE 35, E-PROPOSAL 1, E-QUESTION 4

Fate accounted for: **62 / 62 lexemes** (5 produced a symbol/scope event, 57 discarded with a named rule). Each is listed with its source location in the bundle's `audit` section.

### 3b. Prose summary — the equivalent list

The summary is free text. It contains no record of what it omitted, no link from any sentence to the turn it came from, and no rule explaining an omission. Concretely:

- Drop list in the artifact: **none** — it cannot be produced from the summary alone.
- Lexemes whose fate can be determined from the artifact: **0 / 62**.
- Summary sentences carrying a source pointer: **0**.
- Gold facts missing from the summary (found only by checking against the external gold list, not from the summary itself): none

## 4. Loader: on-demand resolution

Header loaded up front: 174 tokens.

| Follow-up query | Symbols resolved (matched by → status) | Tokens added |
|---|---|---:|
| ok, what should the first paragraph of related work say? | — (none referenced) | 0 |
| what RMSE did XGBoost get? | `evaluation_metric` (value → live) | 63 |
| how are we splitting train and test? | `train_test_split` (keyword → live) | 74 |
| are we including the SHAP plot? | `pages_max_including` (keyword → live) | 89 |

Total after all queries: header 174 + expansions 226 = 400 tokens (vs. 1419 for re-reading the transcript).

## 5. Context IR header (strict) as handed off

```
CTXIR/1 src=heldout_ml_report_chat.txt turns=39 | live=5 const=5 var=0 | dropped=0 (none)
Compiled conversation state. Values are current; ask to resolve(<name>) for original wording/history.
SCOPES (*active, x closed)
*s0 conversation  <- current
CONST
K0 pages_max_including = 6 pages max including references. due date is December 5 [con s0]
K1 evaluation_metric = RMSE [fact s0]
K2 train_test_split = an 80/20 random split [cho s0]
K3 least_15_references = We need at least 15 references [con s0]
K4 zotero_manage_choice = Zotero to manage the references [cho s0]
```
