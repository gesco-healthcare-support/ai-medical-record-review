# How to troubleshoot a summary

Use this when a reviewer reports that one summary is wrong, flagged, cut off, missing from the
export, or replaced by a notice, or when a summarize job ends **Needs attention**. It maps each
symptom to the flag stored on the summary, the log line that explains it, the cause, and what to do.

If a whole job is stuck or failed rather than one summary being wrong, use
[How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md). If the fix turns out to
be a rule change, use [How to change a summary prompt or rule](change-a-summary-prompt-or-rule.md).

## Prerequisites

- The record open in the app, on its Summaries tab.
- A shell on the machine running the Docker stack, in the checkout directory, to read container logs
  and query the database. Summarize jobs log from the `summarize-worker` containers; a single-row
  re-draft and a bundle report run inside the `api` container and log there.
- [Summarization](../explanation/summarization.md) explains every step named below.

## Steps

1. **Identify the summary.** Note the record id (the `<id>` in the `/records/<id>` address) and the
    page range of the summary (clicking the card jumps the viewer to its first page).

2. **Read the chips on the card.** They are derived from stored columns
    (`backend/app/models.py` `Summary.listing()`,
    `frontend/components/review/summaries-view.tsx` `SummaryChips`):

    | Chip | Stored state | Meaning |
    | --- | --- | --- |
    | Edited | Any of `edited_title`, `edited_date`, `edited_text` is set | A reviewer edited it; edits win over everything else |
    | Manual check | `manual_check` | The row flag was `x`, or the body was cut off at the token cap, or the body came from the fallback model |
    | AI-fixed | `verified` and `verified_text` set | The audit's rewrite of the body was applied |
    | Flagged, not applied | `verified`, `verify_issues` set, `verified_text` empty | The audit found problems but its rewrite was rejected by a guard; the body is the model's own |
    | Not checked | `audit_model` set and `verified` false | The audit was requested and did not complete; the body exports unaudited |
    | Category guessed | The review row's categorization method is not one the frontend treats as decided (`frontend/lib/review-rows.ts` `categoryWasGuessed()`) | The summary was written under a category the categorizer guessed; see [Categorization](../explanation/categorization.md) |
    | Pages changed - re-summarize | No review row has this page range any more | The row was merged or re-spanned after this summary was written |
    | Category changed - re-draft to apply | The review row's category differs from the one the summary was written under | |
    | Excluded | `excluded` | Left out of every export |

    The collapsed list under the card ("N things the AI check flagged") shows the audit's issues by
    type: `unsupported`, `contradiction`, `date`, `laterality`, `vitals`, `pain_descriptor`,
    `capitalization`, `range_of_motion`, `duplicate_finding`, `prior_visit`.

3. **Read the stored flags.** Replace `<DOCUMENT_ID>` with the record id:

    ```bash
    docker compose exec postgres psql -U mrr -d mrr -c "SELECT s.idx, s.row_start, s.row_end, s.row_category, s.model, j.model AS job_model, s.audit_model, s.verified, s.verified_text IS NOT NULL AS rewrite_applied, s.verify_issues IS NOT NULL AS has_issues, s.manual_check, s.unreadable, s.embedded_review FROM summaries s JOIN jobs j ON j.id = s.job_id WHERE s.document_id = '<DOCUMENT_ID>' ORDER BY s.idx;"
    ```

    Expected result: one line per summary of the record. `job_model` is the body model the job asked
    for; `model` is the one that answered.

4. **Find the log lines.** Most summary warnings name the page range as `pages <start>-<end>`.
    Replace `41-47` with the summary's range:

    ```bash
    docker compose logs --since 48h summarize-worker | grep "pages 41-47"
    ```

    For a summary produced by **Re-draft**, look in the API's log instead:

    ```bash
    docker compose logs --since 48h api | grep "pages 41-47"
    ```

    Four warnings carry no page range and have to be found by their text: `title unusable`,
    `row title truncated`, `summary verify reply hit the`, and `summary verify failed`.

5. **Match the symptom** in the table below and apply its fix.

## Symptom map

Every log line below is logged at WARNING by `app.services.summarize_engine` unless another logger is
named. Lines marked "Worker" come from `app.worker.tasks`; its `job <id> needs attention` line is
INFO, the per-row failure lines are WARNING. Numbers in angle brackets vary.

| Symptom | Row signal | Log line | Cause | What to do |
| --- | --- | --- | --- | --- |
| Not checked | `audit_model` set, `verified` false | `summary audit did not complete on pages <s>-<e> (truncated=<bool>, output tokens=<n>); shipping the body unaudited` | See the next two rows; with neither of their lines, the body was empty | Read the summary against its source pages before exporting; press **Re-draft** to run the audit again |
| Not checked, audit ran out of room | As above, `truncated=True` | `app.services.summary_verify`: `summary verify reply hit the <cap>-token cap after <n> output token(s); keeping original (unverified)` | The audit's reply must repeat the whole summary, so long summaries (depositions, long treating reports) exhaust its cap | As above. The cap is `AUDIT_MAX_OUTPUT_TOKENS` when set, else `SUMMARY_MAX_OUTPUT_TOKENS`; `AUDIT_MAX_OUTPUT_TOKENS` is not named in `docker-compose.yml` (see [Configuration model](../explanation/configuration-model.md)) |
| Not checked, audit raised | As above, `truncated=False` | `app.services.summary_verify`: `summary verify failed; keeping original: <error>` | The audit call failed, or its reply was not valid JSON | Re-draft; if it keeps failing, check the model service ([How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)) |
| No audit chip and no audit | `audit_model` empty, `verified` false | none | The audit was switched off (`SUMMARY_VERIFY=false`) when the summary was written. Entries in a bundle report are never audited either, and are not stored as summaries | Expected. With the audit switched on, Re-draft audits the summary |
| Body ends mid-sentence | Manual check; `model` equals `job_model`; the row flag is not `x` | `summary body hit the <cap>-token cap on pages <s>-<e> (<n> chars); re-asking at <wider>`, then one of `the wider re-ask returned <n> chars on pages <s>-<e> (truncated=<bool>); keeping it`, `the wider re-ask came back empty on pages <s>-<e>; keeping the truncated body`, `the wider re-ask failed on pages <s>-<e>; keeping the truncated body`. The first line with no second line means the re-ask was truncated too and shorter, so the first reply was kept | The body did not fit the output cap even after one re-ask at the cap times `SUMMARY_TRUNCATION_RETRY_MULTIPLIER` | Check the end against the source and complete it by hand, or Re-draft. Raising `SUMMARY_MAX_OUTPUT_TOKENS` or the multiplier affects every row, and on a thinking model extra budget can go to reasoning (comment on `summary_truncation_retry_multiplier` in `backend/app/config.py`) |
| Deposition covers only part of the transcript | none | `deposition body on pages <s>-<e> cites through page <p> of <m> and stops (<g> group(s) for <n> page(s)); the summary covers part of the transcript` | With a token-cap line before it: truncation (previous row). Without one: the model ended its reply early and reported it finished | Re-draft; if it repeats, summarize the rest by hand |
| Body came from a lesser model | Manual check; `model` differs from `job_model` | `body model <asked> exhausted its retries on 429 for pages <s>-<e>; falling back to <fallback>` | The body model kept refusing with 429 after the retry budget was spent (shared quota pressure, or a spent daily quota), so `SUMMARY_BODY_FALLBACK_MODEL` answered | Read it; Re-draft once the body model is available to get it from the configured model |
| Body misses table, checkbox or handwritten content | none | `multimodal rasterize failed for pages <s>-<e>; using OCR-only: <error>` | Page images could not be produced, so only OCR text was sent | Re-draft; check the source PDF opens and renders |
| Body is only "Unintelligible: the text recognizer could not read ..., so there was no text to summarize." | `unreadable` true, `model` empty; the job ended Needs attention naming the row | OCR warnings for those pages (see [OCR and page text](../explanation/ocr-and-page-text.md)); worker `job <id> needs attention: <n> row(s) could not be summarized` | Every page of the row failed text recognition (errored, not blank) | Press **Summarize** again: an unedited notice is deleted and re-attempted. If it persists, check the scan, or exclude the row |
| Body ends "..., so that content is not covered by this summary." | `unreadable` true, `model` set | OCR warnings for those pages | Some pages of the row failed text recognition; the rest were summarized | A normal Summarize run does not redo a partial row (it holds real content). Press **Re-draft** to regenerate it |
| Body ends `Pages <x>-<y> contain an embedded review of medical records, which is not summarized here.` | `embedded_review` true | none | An excluded records-review row follows this evaluation (category 12 or 13) | Expected. A Re-draft drops the sentence; **Re-summarize all from scratch** restores it |
| Summary is only the title, or lost most of its bold points | Flagged, not applied | `verify pass returned only the title on pages <s>-<e> (issues: <types>); keeping raw body` or `verify pass dropped bold headings on pages <s>-<e> (issues: <types>); keeping raw body` | The guard rejected the audit's rewrite, so the raw body shipped. If the raw body itself is title-like or bare, the body call produced it | Read the issue list; correct the body by hand if the flagged issues are real; Re-draft if the raw body is poor |
| Deposition page groups flattened | Flagged, not applied | `verify pass flattened the deposition grouping on pages <s>-<e>; keeping raw body` | The audit's rewrite merged the page groups, so it was rejected | Nothing to undo: the raw grouped body shipped |
| Job ended Needs attention | Record status "Needs attention"; a banner with the job's message; failed rows highlighted on the Review tab; `jobs.attention` holds `{rows: [{idx, pages, reason}], message}` | Worker: `summarize row <i> permanent failure on document <id>` (with the traceback), then `job <id> needs attention: <n> row(s) could not be summarized` | A row failed permanently: no readable text, unreadable transcript page numbers, a rejected request, a spent daily quota, a deadline, an over-long value, or an unrecognised error; or a row was delivered as a notice | Act on each row's reason (exclude it, fix its pages or category), then press **Summarize**: finished rows are kept and only the named rows run again. Reason texts: [Errors and messages](../reference/errors-and-messages.md) |
| Job ended Needs attention: "No sub-documents could be summarized, so the job stopped rather than retrying." | As above | Worker: `summarize row <i> transient failure on document <id> (<n> in a row)` repeated | At least `SUMMARIZE_GIVEUP_AFTER_FAILURES` transient failures and no success in that attempt: the model service is refusing | [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md); [How to switch model backends](switch-model-backends.md) |
| Header line is the short segmentation title | none | `generated title unusable (<n> chars); falling back to the row title` | The title call returned nothing, or more than 200 characters | Re-draft, or edit the title |
| Header line cut short | none | `row title truncated to <n> chars to fit the column` | The fallback segmentation title was longer than the column allows after decoration | Edit the title |
| Header line changed after the audit | Issue list shows `date`, `laterality` or `unsupported` | `audited title unusable (<n> chars); falling back to the row title` when a correction was rejected | The audit corrects the title independently of the body and stores it in `verified_title` when it differs | Check the corrected title against the source; edit it if wrong |
| Part of a facility name missing from the header line | none | none | `without_address()` removed a piece it took for an address, such as a city before `CA` | Edit the title; a reviewer's title is never rewritten |
| Author's name order or a facility's branch differs from the source | none | none | `tidy_author_and_facility()` writes a surname-first author first-name-first and cuts a listed health system (Kaiser Permanente) to its name | Edit the title to the wording you want; a reviewer's title is never rewritten |
| Provider spelled differently in the export than in the app | none | none | `consistent_authors()` makes one spelling per provider across the record at export | Edit the title to the spelling you want; an edited title is locked and wins its group |
| Facility spelled differently in the export than in the app | none | none | `consistent_facilities()` gives a facility the spelling the same author's other entries carry, when the two are near spellings of one name | Edit the title to the spelling you want; an edited title is locked and wins its group |
| Facility in the header line is broken letters | none | none | OCR garbled a logo or letterhead; the title call reads the facility from the page image and the audit may not write non-words, but an older summary predates both | Edit the title; on a record where the same doctor's other entries name it correctly, the export already uses their spelling when the two have the same number of words |
| An entry is missing from the export | none | none | Same-visit folding merged a category 1 entry into another by the same author on the same date, or the summary is excluded | Check the other entry of that visit; its body carries the folded sections. Include the summary if it was excluded |
| Wrong date of injury in the body | none | none | The `**DOI**:` prefix comes from the review row's date of injury, read at the end of segmentation | Correct the date of injury on the Review tab, then Re-draft the summary |
| Written under the wrong category | Category changed - re-draft to apply | none | The row's category was changed after summarizing | Re-draft |

## Verify it worked

- After a Re-draft or a new Summarize run, the card's chips reflect the new state; run the query in
  step 3 again and check `verified`, `manual_check` and `unreadable` for the row.
- A regenerated summary carries a new `model`, `audit_model` and `prompt_fingerprint` as the
  configuration and prompt now stand.

## If it fails

- **Re-draft reports an error.** The route answers 422 when the row has no readable text, its
  transcript page numbers could not be read, or the PDF cannot be opened, and 503 when text
  recognition is unavailable. The texts are in [Errors and messages](../reference/errors-and-messages.md).
- **Re-draft is refused with 409.** A job is running on the record; wait for it or stop it.
- **Re-draft and Re-summarize discard edits.** Re-draft clears the reviewer's edits to that summary;
  Re-summarize all from scratch clears every summary of the record. Copy any hand-written text first.

## Related pages

- [Summarization](../explanation/summarization.md)
- [How to change a summary prompt or rule](change-a-summary-prompt-or-rule.md)
- [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)
- [Job and document states](../reference/job-and-document-states.md)
- [Model calls by stage](../reference/model-calls-by-stage.md)
- [Configuration](../reference/configuration.md)
- [Data model](../reference/data-model.md)

<!-- reviewed: 2026-09-30 -->
