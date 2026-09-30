# frontend/components/review - agent instructions

The workbench. Read `docs/explanation/frontend-workbench.md` before changing state flow here or in
`frontend/hooks/use-review-workflow.ts`.

## Invariants

- Every row edit goes through `ReviewEditor`'s `field()`, which applies `moveSharedBoundary()` then
  `clearFlagOnEdit()`. Do not call `onRowsChange()` from `RowsTable` directly.
- The row buffer is owned by the hook. Replace it wholesale only through the hook's `replaceRows()`;
  never reset `keySeq` (`frontend/lib/review-rows.ts`).
- Any component that writes rows on the server (a duplicate resolve, a summary's category) must call the
  hook's `reloadRows()` afterwards (`onResolved`, `onRowsChanged`). Otherwise the next autosave sends
  the old values back.
- Filters hide rows INSIDE the `RowsTable` map. Never narrow the `rows` array: numbering is `i + 1` and
  the gap strips come from the running previous end.
- Match attention rows by the `"start-end"` page range, never by `idx` (it counts included rows only).
- Summarize is gated on a CURRENT duplicate check (`!checked || stale` blocks it). "Summarize without
  checking" appears only when that is the only blocker, confirms, and sends
  `skip_duplicate_check: true`. Never send it by default.
- Stop is two-stage. The first press is cooperative; "Force stop" appears only after the server's
  `graceSeconds`. Reset the escalation on BOTH `watching` and `activeJobId`, and clear its timer.
- Anything that regenerates summaries must invalidate `["summaries", id]`.
- Summary-card optional fields (`verifyKeptRaw`, `verifyFailed`, `rowMissing`, `rowMethodLive`) mean
  "nothing to say" when absent. Coalesce with `??`, not `!== null`.
- Chip order in `SummaryChips` is fixed: reviewer actions, system flags, staleness, then `Excluded`.
- The doctor select must always include the stored value, or a blur writes a different doctor.
- Files go through `downloadFile()` and are watched with `useDownloadWatch()`; keep the dialog's footer
  `<output>` always mounted so screen readers announce changes.

## Mirrors of backend code (change both sides together)

- `rowErrors()` <-> `backend/app/services/rows.py` `validate_rows()`.
- The DOI prefix patterns in `summaries-view.tsx` <-> `backend/app/services/summary_doi.py`.
- `INLINE_RE` in `markdown-text.tsx` <-> `backend/app/services/reporting.py` `INLINE_EMPHASIS_RE`,
  character for character, with NO `s` flag.
- Trailing-marker regexes in `summaries-view.tsx` must stay linear-time (match the marker, then
  trim); `summaries-view.title.test.ts` checks a long whitespace-only title returns promptly.

## PDF viewer

- pdf.js is vendored in `frontend/public/pdfjs/` (not an npm dependency). The iframe must stay
  same-origin: page sync and `jumpTo` read `PDFViewerApplication` inside it.
- Hide pdf.js controls only with inline `display: none !important` on the element; an injected
  stylesheet loses to pdf.js's own rules. Only the markup editors are hidden.
- After upgrading pdf.js, check `#editorModeButtons`, `#editorModeSeparator`, `pdfViewer.pagesCount`,
  `page` and the `pagechanging` / `pagesloaded` events still exist.

## Tests

- `window.confirm` is used here: stub with `vi.spyOn(window, "confirm")` and restore it in the same file.
- For the 800 ms autosave, advance fake timers inside `act`, then switch back to real timers in the
  same test (see `editAndRunTheDebounce` in `hooks/use-review-workflow.test.tsx`).
- `SplitPane` reads `localStorage`; `vitest.setup.ts` supplies one on Node versions that lack it.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/review hooks/use-review-workflow lib/review-rows
pnpm typecheck
pnpm build
```

## Docs

- `docs/explanation/frontend-workbench.md`
- `docs/reference/frontend-routes-and-data.md`
- `docs/how-to/extend-the-frontend.md` (row field, header field, summary flag)

<!-- reviewed: 2026-09-30 -->
