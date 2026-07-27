---
feature: Each workbench tab shows its own step's actions (Review -> Duplicates -> Summaries)
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

The record workbench header shows the actions belonging to the visible step: Review & correct offers
"Re-run segment" + "Check duplicates", Duplicates offers "Re-check duplicates" + "Summarize", and
Summaries offers "Re-summarize all" - so the reviewer is walked through the duplicates gate before
summarizing.

## Context & decisions

Why now: today the header shows "Re-run segment" on ALL three tabs and Summarize only on Review &
correct (`frontend/components/review/review-page-client.tsx:136-158`), so a reviewer can summarize
without ever opening Duplicates - which is how the duplicate-wipe and missed-duplicate problems went
unnoticed.

Re-planned against main `16614fe` (after PR #43 merged). New finding from that re-read: #43 moved the
backend advisory count (`_unreviewed_dupe_count`) and the cluster chip to "not dismissed AND 2+ copies
still included", but `review-page-client.tsx:31-33` still computes its OWN banner count with the old
"no copy is marked kept" rule - so the header banner can now disagree with the cards it points at.
This plan owns that file, so aligning the two is folded in as Task 1b.

Resolved decisions:
- Decision: the header banner count uses the same rule as the chip and the API (not dismissed AND 2+
  included copies), because one definition of "needs review" must drive every surface; duplicating a
  second rule in the page component is what let them drift.
- Decision: Summarize MOVES to the Duplicates tab (it is not duplicated on Review & correct) because
  the point of the change is that duplicates are reviewed before summarization; a record with no
  clusters costs one extra click and the Duplicates tab already states "No duplicates".
- Decision: "Re-run segment" appears ONLY on Review & correct, because re-segmentation is that
  step's action and it discards the reviewer's corrections (`use-review-workflow.ts:270-289`
  confirms first) - it does not belong on a later step's toolbar.
- Decision: "Re-summarize all" MOVES to the Summaries tab because it discards existing summaries and
  their edits; it belongs beside the summaries it replaces, not on the duplicates step.
- Decision: "Check duplicates" only switches tabs - it never starts a dedup job, because Adrian's
  standing rule is no automatic AI calls; the Duplicates tab's own "Re-check duplicates" is the
  explicit run (and dedup already auto-chains after segmentation).
- Decision: the `stale` banner in `duplicates-view.tsx:72-87` keeps its explanatory text but loses
  its button, because the header now always offers "Re-check duplicates" and two identical buttons
  on one screen is noise.
- Decision: Summarize and Re-check duplicates are disabled while a dedup job is queued/running,
  because `summarize_start` and `dedup_start` both 409 on an active job
  (`backend/app/api/documents.py:563-566`, `:476-477`) - a disabled button with a hint beats a
  surfaced 409.

## All needed context

- Header actions block: `frontend/components/review/review-page-client.tsx:114-161`; `tab` state at
  :29; `dupData` (already fetched in this component, shared react-query key) at :28; the
  `summarizeDisabled` + `summarizeHint` logic at :78-86; `reSummarizeAll` confirm at :88-96; the
  duplicates nag banner at :165-177 (its "Review duplicates" button stays).
- `useStartDedup(documentId)` (`frontend/hooks/use-duplicates.ts:35-41`) - POST `/dedup/start`,
  invalidates the duplicates query on success. Mirror `DuplicatesView`'s usage
  (`duplicates-view.tsx:23,33-40`) including `humanizeError` for the failure message.
- `wf.onSummarize()` flushes the hook's rows, so it works unchanged from the Duplicates tab
  (`frontend/hooks/use-review-workflow.ts:293-305`); duplicate resolutions already call
  `wf.reloadRows` (`review-page-client.tsx:244`), so those rows are current.
- Job state to gate on: `dupData?.job?.state` is `"queued" | "running"` while a check is in flight
  (same test `DuplicatesView` uses at `duplicates-view.tsx:27`).
- Tests: `frontend/components/review/review-page-client.test.tsx` (mocks the two views and the
  workflow hook), `frontend/components/review/duplicates-view.test.tsx` (mocks `use-duplicates`;
  currently asserts the banner button - update it).

## Tasks (implementation blueprint)

### Task 1 - per-tab header actions
- what: MODIFY `frontend/components/review/review-page-client.tsx`: add
  `const recheck = useStartDedup(documentId)` and
  `const dedupRunning = dupData?.job?.state === "queued" || dupData?.job?.state === "running"`;
  restructure the non-watching branch of `.rce-bar-actions` so that
  (a) `tab === "review"` renders the existing outline "Re-run segment"/"Segment" button plus a new
  primary button "Check duplicates" whose `onClick` is `() => setTab("duplicates")`;
  (b) `tab === "duplicates"` renders an outline "Re-check duplicates" button (disabled while
  `recheck.isPending || dedupRunning`, calling `recheck.mutateAsync()` inside a try/catch that sets
  `wf.setBanner(humanizeError(err, { fallback: "Could not start the check - please try again." }))`)
  plus the existing primary Summarize button, with `summarizeDisabled` extended by `dedupRunning`
  and a matching hint "Wait for the duplicate check to finish.";
  (c) `tab === "summaries"` renders the existing ghost "Re-summarize all" button when
  `summaries.length > 0`.
  Keep the autosave indicator rendering only on the Review tab (`:125`).
- pattern: the existing conditional action block at `review-page-client.tsx:123-159`.
- approach: test-after
- acceptance (EARS):
  - WHILE the Review & correct tab is active, THE SYSTEM SHALL show exactly "Re-run segment" (or
    "Segment") and "Check duplicates", and SHALL NOT show Summarize or Re-summarize all.
  - WHEN the reviewer clicks "Check duplicates", THE SYSTEM SHALL switch to the Duplicates tab
    without starting a job.
  - WHILE the Duplicates tab is active, THE SYSTEM SHALL show "Re-check duplicates" and the
    Summarize button labelled with the included-document count.
  - WHEN the reviewer clicks "Re-check duplicates", THE SYSTEM SHALL POST `/dedup/start` once.
  - WHILE a dedup job is queued or running, THE SYSTEM SHALL disable both "Re-check duplicates" and
    Summarize.
  - WHILE the Summaries tab is active, THE SYSTEM SHALL show "Re-summarize all" when at least one
    summary exists, and SHALL NOT show "Re-run segment".

### Task 1b - one definition of "needs review"
- what: MODIFY `frontend/components/review/review-page-client.tsx:31-33`: count a cluster when
  `!c.dismissed && c.rows.filter((r) => r.include !== false).length >= 2`, matching
  `_unreviewed_dupe_count` (`backend/app/api/documents.py:376-388`) and `ClusterCard`
  (`frontend/components/review/duplicates-view.tsx:130-134`).
- pattern: the `includedCount` computation in `ClusterCard`.
- approach: test-after
- acceptance (EARS):
  - WHEN a cluster has 2+ included copies and is not dismissed, THE SYSTEM SHALL include it in the
    header banner count.
  - WHEN a cluster has at most one included copy, THE SYSTEM SHALL NOT count it, whether or not a
    copy is marked kept.

### Task 2 - drop the duplicated banner button
- what: MODIFY `frontend/components/review/duplicates-view.tsx`: keep the `stale` banner text,
  remove its "Re-check duplicates" button and the now-unused `useStartDedup` import, `recheck`
  mutation and `onRecheck` handler.
- pattern: the banner block at `duplicates-view.tsx:72-87`.
- approach: test-after
- acceptance (EARS):
  - WHEN the duplicates data is stale, THE SYSTEM SHALL show the "boundaries changed" hint text.
  - WHEN the duplicates data is stale, THE SYSTEM SHALL NOT render a second re-check button inside
    the view body.

### Task 3 - tests
- what: EXTEND `frontend/components/review/review-page-client.test.tsx`: assert the visible buttons
  per tab, that "Check duplicates" switches tabs, that "Re-check duplicates" calls the start-dedup
  mutation, and that a running dedup job disables both Duplicates-tab buttons. UPDATE
  `frontend/components/review/duplicates-view.test.tsx` so the stale case asserts the text and the
  absence of the button.
- pattern: the existing render + `screen.getByRole("button", { name: ... })` style in
  `review-page-client.test.tsx`.
- approach: test-after
- acceptance (EARS): The system shall pass the full frontend suite with new-code coverage >= 80%.

## Validation loop

1. FE: `pnpm -C frontend typecheck`
2. FE: `pnpm -C frontend exec vitest run` (full suite)
3. Live (local :8080) with Playwright MCP: open a record -> Review & correct shows Re-run segment +
   Check duplicates only -> click Check duplicates -> Duplicates tab shows Re-check duplicates +
   Summarize -> click Re-check duplicates -> both buttons disable while the job runs and the
   countline shows progress -> Summaries tab shows Re-summarize all + Export.

## Risk / rollback

- Blast radius: the workbench header for all three tabs, plus the duplicates stale banner. No
  backend change.
- Behaviour change to communicate: Summarize is no longer on Review & correct.
- Rollback: revert the PR (frontend-only, no data).
