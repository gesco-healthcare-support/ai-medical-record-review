# Plan: alocker tester feedback -- format, DOI, and content-scope fixes

Status: decisions resolved with Adrian 2026-07-31, nothing implemented.
Baseline `origin/main` `9cf1d03`.
Evidence: `W:\MRR_Research_and_Analysis\03_Reports\TESTER_FEEDBACK_TRIAGE_2026-07-31.md`.

## 0. Decisions (all resolved -- do not re-litigate)

| # | Decision | Chosen |
| --- | --- | --- |
| 1 | Audit destroying bold labels | **Prompt carve-out AND code guard.** An incorrect heading is CORRECTED in place -- renamed or re-cased -- and stays bold. Deletion is not a permitted repair for a heading. The audit fact-checks and corrects; it does not blindly remove or add |
| 2 | Category 5 format ambiguity | **Add a procedure-session document type AND narrow the prose escape hatch** |
| 3 | "Only positives" scope | **Extend the generation rule only** -- no new audit rule (a new audit rule is the mechanism that caused defect 1) |
| 4 | DOI extraction window | **`_MAX_PAGES` 5 -> 10** |
| 5 | Interim History | **Category 1 only** -- not 12/13, which are required to carry the full injury history |
| 6 | `CT:` regex bug | Fix; no decision needed (pure defect, reproduced locally) |
| 7 | Guard behaviour when it fires | **Reject the body rewrite only when a heading disappeared and every issue is `capitalization` or `range_of_motion`** -- the two correction-only house rules (see 1.2) |

Decision 7 was settled on the evidence rather than by asking: house rule 5 (`duplicate_finding`)
legitimately removes a whole point, and an `unsupported` finding may legitimately restructure one,
so a blanket "no label may ever disappear" guard would suppress real faithfulness fixes. Blocking
only cosmetic rewrites keeps both properties.

## 1. Task 1 -- stop the audit destroying bold point headings (URGENT, live regression)

`approach: code`

**Why first.** Current-build measurement: 7 of 16 audited summaries lost bold labels (43.8%), 5 of
them lost all labels; the older cohort was 7.3% (11 of 151). When the audit raises a
`capitalization` issue the loss rate is 57.1% (4 of 7) against 9.5% (14 of 147) when it does not.
`_HOUSE_RULES` landed 2026-07-30 in `dc3bc12` (PR #61). This makes current output worse than last
week's, and it is the defect the tester actually hit.

**The key fact.** `house_style.sentence_case_caps_runs` (PR #62 `f3db55d`, landed AFTER #61)
already re-cases capitals mechanically, preserves allowlisted acronyms, title-cases organisation
names, and is bold-aware (`_SENTENCE_START` matches `\*\*`). It runs on the raw summary at
`summarize_engine.py:504` and on the audit's output at `summarize_engine.py:540`. So the audit's
capitalisation rule buys almost nothing that code does not already guarantee, while costing the
labels.

### 1.1 Rewrite house rule 3 -- `summary_verify.py:39`

Mirror the pattern of rule 4 ("Do not remove or alter the measured number; add the direction if it
is missing"), which already expresses "correct in place, never delete".

The rewritten rule must state:
- The correction for capitals is **re-casing**: title case for an organisation or facility name,
  sentence case otherwise. Removing or rewording the text is never the fix.
- A `**Heading**:` point heading that is wrong -- misnamed, mis-cased, or not one of the points the
  category asks for -- is **corrected in place**: rename it or re-case it, and keep it bolded.
  Deleting the heading, unbolding it, or folding its content into prose is not a permitted repair.
- The existing acronym allowlist and title exemption stay as they are.

### 1.1b State the general principle once, at the top of `VERIFY_PROMPT`

The narrower rule above only patches the symptom. The audit's remit needs saying outright, because
the same misreading can recur under any rule: **the audit fact-checks and corrects; it does not
blindly remove or add.** Where something is wrong, the repair is an edit in place.

Deletion is permitted in exactly two situations, and the prompt should say so:
- the content is unsupported by, or contradicts, the SOURCE; or
- a house rule explicitly directs removal -- rules 1 (height and weight), 2 (pain quality words),
  5 (duplicated Findings/Impression) and 6 (previous-visit recap).

Rules 3 (capitalisation) and 4 (range of motion) are **correction-only**, and rule 4 already says so
("Do not remove or alter the measured number; add the direction if it is missing"). Rule 3 is being
brought into line with a rule that already works.

Because `_HOUSE_RULES` and `HARDENING_PREAMBLE` are documented as needing to be edited together
(`summary_verify.py:22-30`), check the generation-side bold rule (`_F_BOLD`, reached from
`build_preamble`) still agrees after the edit. It should need no change -- it already forbids
bolding whole sentences -- but confirm rather than assume.

### 1.2 Add the code guard -- `summarize_engine.py:536-541`

At the point where `result["issues"]` is accepted:

- Count bold headings in the raw `summary` and in `result["fixed_text"]`. A heading is a `**...**`
  span; count spans, not `**` occurrences, so an unbalanced marker cannot inflate the count.
- **Compare counts, never heading text.** This is deliberate: renaming or re-casing a heading is the
  behaviour we want and must pass the guard untouched. Only a drop in the count -- a heading that
  stopped existing -- is the defect.
- If the rewrite has **fewer** headings than the raw text AND every reported issue type is in
  `{capitalization, range_of_motion}` (the two correction-only house rules), **do not** accept
  `fixed_text`. Keep the raw body.
- `vitals` and `pain_descriptor` are deliberately **excluded** from that set even though they read as
  cosmetic: rule 1 removes height and weight and rule 2 removes pain quality words, so either can
  legitimately empty a point and take its heading with it. Blocking those would suppress a correct
  fix.
- Still store `verify_issues` in that case, so the reviewer sees what was flagged, and log at
  WARNING with the row's page range and the issue types, so the guard's firing rate is measurable.
- The **title** correction is independent: accept `fixed_title` whether or not the body rewrite is
  rejected.
- Any other issue type present (`unsupported`, `contradiction`, `date`, `laterality`, `prior_visit`,
  `duplicate_finding`, `vitals`, `pain_descriptor`) means the audit had a real reason to remove
  content -- accept the rewrite unchanged.

**Acceptance (EARS)**

- WHEN the audit returns a body with fewer bold headings than the raw summary and every issue is in
  `{capitalization, range_of_motion}`, THE SYSTEM SHALL store the raw body as the summary text,
  store the reported issues, and emit a WARNING naming the page range and issue types.
- WHEN the audit returns a body with fewer bold headings and at least one issue lies outside that
  pair, THE SYSTEM SHALL store the audited body.
- WHEN the audit **renames or re-cases** a heading without reducing the heading count, THE SYSTEM
  SHALL store the audited body -- correcting a wrong heading is the intended behaviour, not a defect.
- WHEN the audit returns a body with the same or more bold headings, THE SYSTEM SHALL store the
  audited body, unchanged from today's behaviour.
- WHEN the audit corrects only the title, THE SYSTEM SHALL store the corrected title regardless of
  whether the body rewrite was rejected.

**Tests** (none need Vertex -- stub `verify_summary`)

1. `capitalization`-only issues plus heading loss -> raw body kept, issues stored, warning logged.
2. `unsupported` present plus heading loss -> audited body kept.
3. **Heading RENAMED, count unchanged, `capitalization`-only issues -> audited body kept.** This is
   the test that pins the correction-not-deletion distinction; without it a naive guard that
   compared heading text would block the behaviour we are asking for.
4. `vitals` issue that empties a point and drops its heading -> audited body kept.
5. No heading loss -> audited body kept (regression guard on existing behaviour).
6. Body rejected but title corrected -> corrected title still stored.
7. Raw summary with no bold headings at all -> guard never fires, audited body kept.
8. Replay of the real row 252 shape (two headings -> zero, two `capitalization` issues) -> rejected.

## 2. Task 2 -- category 5 format ambiguity

`approach: code`

**Evidence.** Four Extracorporeal Shockwave Treatment rows in one document, all category 5, all the
same title, generated 2 labelled / 2 prose. The type matches none of category 5's eight document
types, so it falls to the `Daily Encounter or SOAP Note` catch-all, whose parenthetical reads "If
the points below are not in the document, just summarize what treatment was given" and whose third
example is bare prose. Both formats are currently sanctioned.

Edit `prompts.py` `category_05` (element-wise -- see section 5):

- **Add a procedure-session document type** covering extracorporeal shockwave therapy, injections
  and comparable single-procedure treatment sessions, with the point set the tester confirmed:
  Diagnosis (if present), Body part being treated, Treatment provided.
- **Narrow the escape hatch**: prose is the fallback only when neither a body part nor a treatment
  can be identified in the document. Where either is present, the labelled points are required.
- **Leave the third example in place.** It is a legitimate length exemplar from PR #55 (`45499bc`)
  tracking the measured human median of 165 characters for this category; the fix is to say when it
  applies, not to delete it.

**Acceptance (EARS)**

- WHEN a category 5 document names a body part or a treatment, THE SYSTEM SHALL emit the labelled
  point form.
- WHEN a category 5 document names neither, THE SYSTEM SHALL emit the prose form.
- WHEN the category 5 prompt is rendered, THE SYSTEM SHALL contain exactly one document-type block
  whose points are Diagnosis, Body part being treated, and Treatment provided for procedure
  sessions.

**Note for the record, not a change here.** The 55-deliverable human corpus (n=392 category 5
entries) uses `Diagnosis` 32%, `Plan` 31%, `Subjective Complaints` 11%, and never uses `Body part
being treated` or `Treatment provided`. Those two come from the folded-in category 6 business
spec. The tester endorses them, so they stay; the divergence from the eData corpus is recorded so a
later corpus-alignment pass does not treat it as a defect.

## 3. Task 3 -- extend "only positives" to absences, refusals, and inconclusives

`approach: code`

**Evidence.** `_C_NORMAL_FINDINGS` (`summarize_engine.py:101`) bans only "normal, negative,
unremarkable, or within normal limits". Current-build instances that slipped through: "He denied
anterior pressure, chest tightness, fever, or chills" (category 100) and "**Physical Exam**: The
genitalia/rectal exam was refused" (category 1). Older-cohort prevalence outside the verdict
categories: 236 of 1483 (15.9%); current build 2 of 20, too small to compare.

Extend `_C_NORMAL_FINDINGS` to also omit:
- an explicit absence attached to a named point ("No Known Allergies", "denies fevers and chills") --
  report the point only when there is something to report;
- a test, examination or treatment that was refused, declined, deferred, or not performed;
- a result reported as inconclusive or non-diagnostic.

**The boundary that must not move.** `_C_VERDICT` (`summarize_engine.py:109`) deliberately
*requires* a normal or negative verdict for categories 3 and 14, because for a diagnostic study the
verdict is the content -- half of human imaging entries state a normal impression and a third
contain nothing else. `build_preamble` already sends `_C_NORMAL_FINDINGS` and `_C_VERDICT` to
disjoint category sets except for unknown ids, which receive both. The new wording must therefore
carry its own carve-out sentence so an unknown-id category that receives both blocks is not told to
omit the very verdict the other block demands.

No audit-side rule. Decision 3.

**Acceptance (EARS)**

- WHEN a document states an absence, a refusal, or an inconclusive result and the category is not 3
  or 14, THE SYSTEM SHALL omit it from the summary.
- WHEN the category is 3 or 14, THE SYSTEM SHALL report the stated impression, result, or verdict
  even where it is normal, negative, or inconclusive.
- WHEN `build_preamble` is called with an id in neither known set, THE SYSTEM SHALL emit both blocks
  without them contradicting each other.

**Tests**: `build_preamble("3")` and `build_preamble("14")` still contain the verdict block and not
the widened omission; `build_preamble("1")` contains the widened wording; `build_preamble("999")`
contains both blocks and the carve-out sentence.

## 4. Task 4 -- DOI extraction

`approach: code`

### 4.1 Raise the window -- `summary_doi.py:25`

`_MAX_PAGES` 5 -> 10. Measured: capture on rows whose DOI label is followed by a digit is 83.5%
(n=79) for spans of 1-5 pages and 59.5% (n=37) for 6+ pages. 85.9% of all summarized
sub-documents are 1-5 pages, so only 14.1% see a larger payload, and 10 pages covers 96.2% in full.
Update the comment at `summary_doi.py:23-24`, which currently justifies the value of 5, to state the
measurement behind 10.

### 4.2 Fix the `CT:` marker loss -- `summary_doi.py:46`

`_ITEM`'s `(?P<ct>CT\s*)?` does not admit a colon or dots after `CT`, so a cumulative-trauma period
silently degrades to a bare date range. Reproduced locally with no model call:

```
'CT 11/30/2015 - 12/04/2025'   -> 'CT 11/30/15-12/04/25'   correct
'CT: 11/30/2015 - 12/04/2025'  -> '11/30/15-12/04/25'      marker lost
'C.T. 11/30/2015-12/04/2025'   -> '11/30/15-12/04/25'      marker lost
```

CAMPUS_NIKKI pages 236 carries `Date of injury: CT: 11/30/2015 - 12/04/2025` in its OCR, so the
variant is real, not hypothetical. Widen the group to admit an optional colon and internal dots,
and keep it anchored so a bare "C" or "T" cannot match.

**Do NOT** add label synonyms to `_ISOLATION_PROMPT` (`summary_doi.py:27`). The tester proposed it,
but the data contradicts it: `D/I` is absent from the prompt and captures 2/2, while `date of
injury` is named verbatim and captures 52.5% (64 of 122). Vocabulary is not the binding constraint.

**Acceptance (EARS)**

- WHEN a sub-document is 6 to 10 pages long and states its injury date on page 6 or later, THE
  SYSTEM SHALL include that page in the isolated extraction payload.
- WHEN a model reply states a cumulative-trauma period as `CT:`, `C.T.` or `CT`, THE SYSTEM SHALL
  store it as one item prefixed `CT `.
- WHEN a model reply states a bare date range with no CT marker, THE SYSTEM SHALL store it as a
  range without inventing the marker.

**Tests**: `_clean` over `CT:`, `C.T.`, `CT`, a bare range, a single date, `-`, and two dates joined
with ` & `; and that `_MAX_PAGES` bounds a 30-page row to 10 pages.

## 5. Task 5 -- name Interim History in the category 1 prompt

`approach: code`

`Interim History` appears in no prompt in the catalog. It occurs in 12 rows across 4 documents
(1.2% of the 1030 rows with stored OCR), concentrated in category 1 (9 rows), with 2 in category 12
and 1 in 13. Category 1 only, per decision 5.

Add to `category_01`: where a follow-up or re-evaluation report carries an Interim History (or
Interval History) section, use it as the source for the History of Present Illness point instead of
the full HPI, because it states only what changed since the last visit. This works with the grain
of the existing current-visit rule (`_CURRENT_VISIT_CATEGORIES`, `summarize_engine.py:264`) rather
than against it -- Interim History is the source's own version of the same instruction.

**Acceptance (EARS)**: WHEN a category 1 document contains an Interim History section, THE SYSTEM
SHALL populate the History of Present Illness point from it rather than from the full history.

## 5b. Editing prompts safely -- read before touching `prompts.py`

The catalog is DB-first (`catalog.get_prompt()`), and the server's `prompts` table currently holds
0 rows, so code is the live source of truth **today**. An admin can create a DB row at any time.
Two consequences:

- Edit `prompts.py` **element-wise** -- change the one dict value, never regenerate the file.
- A `taxonomy.py` change reaches an existing category only via a migration that updates the
  `categories` rows. Tasks 2 and 5 change PROMPTS, not the taxonomy, so no migration is needed.
  Do not add taxonomy examples as part of this work.

## 6. Sequence

1. Task 1 (audit guard + rule 3) -- urgent, and it must land before anything else so that later
   measurements are not confounded by label destruction.
2. Task 4 (DOI) -- self-contained, touches only `summary_doi.py`.
3. Task 3 (only positives) -- touches `summarize_engine.py`, same file as task 1; sequence after it.
4. Task 2 and task 5 (prompt text) -- touch only `prompts.py`.

## 7. Validation loop

```bash
docker compose -f docker-compose.dev.yml up -d postgres redis
cd /c/src/mrr-ai && set -a; . ./.env; set +a
export DATABASE_URL="postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr"
cd backend && uv sync --extra docs && uv run alembic upgrade head
uv run pytest -q
uv run ruff check app/ && uv run ruff format --check app/
```

`.env` is at the repo root, not `backend/`. `pypdf` is in the `docs` extra. The CI gate is
"Ruff lint + **format** check" -- `ruff check` alone is not enough. No migration in this plan.

Backend only, so no `ng build` / `ng test` is required.

## 8. How we will know it worked

Re-run the conformance scorer against a post-deploy cohort and compare, filtered to the build under
test (`W:\MRR_Research_and_Analysis\90_Scripts\11_score_app_baseline.py`):

| Measure | Now (current build) | Target |
| --- | --- | --- |
| Audited summaries losing bold labels | 7 of 16 (43.8%) | 0, with the guard's firing rate logged |
| Category 5 same-type rows sharing one format | 2 of 4 | 4 of 4 |
| Non-verdict summaries carrying a forbidden negative | 2 of 20 | fewer, on a larger sample |
| DOI captured, label followed by a digit, 6+ page span | 59.5% (n=37) | toward the 83.5% short-span rate |

Every one of these has a small denominator today. None should be reported as proven until a fresh
cohort of comparable size exists -- the 2026-07-31 caps-run result is precisely the error to avoid
repeating: it improved while the property underneath it broke.

## 9. Blocked on

The working tree is on `feat/summary-category-redraft` with 14 modified files belonging to another
session. This work needs a clean tree branched off `main`. Do not stash, restore, or switch off
that branch -- coordinate first.

## 10. Out of scope

- CAMPUS_NIKKI pages 36-39, "ED PROVIDER NOTE", classified category 100 although taxonomy change
  D-03 moved emergency-department notes into category 1. One instance, not investigated. It is a
  taxonomy/classification question and would need a migration, which this plan deliberately avoids.
- DOI label vocabulary (`Date and Hour of Injury`, `D/I`). Measured and rejected -- see 4.2.
- Any backfill of already-stored summaries.

---

## 11. Implementation notes (2026-08-03)

Section 9 is resolved: the stop-and-restart work landed as PR #69 (`16ff15c`), so this was built on a
clean tree branched off `main`.

All five tasks implemented in the planned order. Backend loop green: 491 passed, `ruff check` and
`ruff format --check` clean.

### Task 3 does NOT reach category 100 -- open, needs a decision

The plan's EARS reads "and the category is not 3 or 14", but the mechanism it specifies (widen
`_C_NORMAL_FINDINGS`) cannot deliver that, because `build_preamble` sends that block only to
`_EXAM_CATEGORIES` = {1, 2, 5, 6, 12, 13}. Verified by rendering every category:

```
cat    1: normal_findings=True   verdict=False
cat  100: normal_findings=False  verdict=False     <- one of the plan's own two examples
cat 4/7/8/10/11: normal_findings=False verdict=False
cat 3/14: normal_findings=False  verdict=True
```

One of the two current-build instances cited in section 3 is **category 100** ("He denied anterior
pressure, chest tightness, fever, or chills"), and category 100 receives neither block. So that
instance is NOT fixed by this change, and the widened rule reaches only the six exam categories.

Not resolved here, because the fix is a scope decision rather than a defect: `exam` gates
`_C_VITALS`, `_C_PAIN` and `_C_RANGE_OF_MOTION` from the same flag, so adding 100 to
`_EXAM_CATEGORIES` would also start instructing it about vitals, pain scales and range of motion.
Options are (a) add 100 (and possibly 4/7/8/10/11) to `_EXAM_CATEGORIES`, (b) split the omission rule
onto its own category set independent of `exam`, or (c) accept the narrower scope. Needs Adrian.

### Incidental defect fixed in the same regex (task 4.2)

Widening the CT group also required anchoring it, which fixed a second latent bug the triage had not
found: `CT\s*` matched the letters INSIDE a word, so a reply reading `IMPACT 11/30/2015 - 12/04/2025`
came back marked `CT 11/30/15-12/04/25` -- inventing a cumulative-trauma classification out of a word
fragment, in a medical-legal field. `\bC\.?T\.?\s*:?\s*` fixes both directions. Pinned by
`test_clean_does_not_read_a_ct_marker_out_of_a_surrounding_word`.

### Every guard/regex test was verified to fail on the unfixed code

The three heading-guard tests fail with the guard neutralised (and only those three, out of 71 in that
file), so they pin the property rather than merely passing alongside it. The CT cases were run against
both the old and new patterns side by side before the change was made.

### Section 8 measures are NOT reported as moved

No cohort has been re-run. Nothing in section 8 is claimed as fixed; the guard's firing rate now logs
at WARNING with the page range and issue types, which is what makes the first measure measurable at
all once a fresh cohort exists.
