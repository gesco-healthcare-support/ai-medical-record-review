---
feature: PDF viewer + clickable rows on the Duplicates and Summaries tabs
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

The Duplicates and Summaries tabs each show the same PDF viewer as Review & correct beside their
list, and clicking a duplicate copy or a summary card jumps that viewer to the sub-document's first
page - so a reviewer can judge duplicates and check summaries against the source without leaving the
tab.

## Context & decisions

Why now: today only Review & correct pairs its table with the viewer
(`frontend/components/review/review-editor.tsx:205-232`); on the other two tabs the reviewer has no
way to look at the pages being talked about.

Resolved decisions:
- Decision: each tab mounts its OWN `PdfViewer` instance (rather than hoisting one shared viewer
  into `review-page-client`) because the tab bodies are conditionally rendered and hoisting would
  restructure `ReviewEditor`'s working SplitPane plus the page shell; the cost is an iframe remount
  (a 304 + re-render, ~1-2s on a large record) when switching tabs, which is acceptable for a
  reading action.
- Decision: reuse `SplitPane` with a DISTINCT `storageKey` per tab (`mrr.duplicates.split`,
  `mrr.summaries.split`) because the useful split differs per tab and the component already persists
  per key (`split-pane.tsx:31-42`).
- Decision: the row container gets the click handler AND the row's title becomes a real `<button>`,
  with the existing action buttons calling `stopPropagation`, because a card/li is not an
  interactive element (so no invalid nested buttons) while the title button keeps the jump reachable
  by keyboard and assistive tech.
- Decision: an editing summary card does NOT jump, because clicks there land in its inputs.
- Decision: fix `PdfViewer.jumpTo`'s `page === lastPage` early-return (`pdf-viewer.tsx:72`) as part
  of this work: once the viewer is ready the jump is applied unconditionally, so re-clicking a row
  after scrolling away returns to it. The guard is kept only for the not-yet-ready path, where it
  prevents a redundant iframe reload.

## All needed context

- `PdfViewer` + `PdfViewerHandle` (`frontend/components/review/pdf-viewer.tsx:5,33`); imperative
  handle at :68-85; the iframe loads `/pdfjs/web/viewer.html?file=/api/documents/{id}/pdf`.
- Pattern to mirror exactly: `review-editor.tsx:38` (`const pdfRef = useRef<PdfViewerHandle>(null)`),
  :53-56 (`select()` -> `pdfRef.current?.jumpTo(...)`), :205-232 (`SplitPane` with
  `right={<div className="rce-viewer"><PdfViewer ref={pdfRef} ... /></div>}`).
- Data already present: duplicate rows carry `pages.start` (`backend/app/api/documents.py:431`,
  type `DuplicateRow` in `frontend/lib/types.ts:110-118`); summaries carry `row.start`
  (`SummaryItem.row`, `types.ts:106`).
- `DuplicatesView` (`frontend/components/review/duplicates-view.tsx`, line numbers re-verified on
  main `c8e7282` after #44): section wrapper at :45, stale banner at :62-69, cluster list at :82-93,
  `ClusterCard` at :98-160 (`<li className="dupe-copy">` at :130, title span at :137, "Keep this one"
  button at :138-147, the `includedCount`/`resolved` chip logic from #43 at :112-126).
- `SummariesView` (`frontend/components/review/summaries-view.tsx`): section + column at :129-130,
  card list at :164-287, editing card at :176-219, read-only card at :221-285 (heading at :231-233,
  action buttons at :253-278), `ExportDialog` at :323-330, `PAGE_SIZE = 20` pagination at :290-320.
- Both views are rendered only by `review-page-client.tsx:287-295` (moved by #44), which holds
  `wf.filename` - pass it down as an OPTIONAL prop so the existing tests that omit it keep compiling.
- Re-planned against main `c8e7282`. #44 changed the surrounding UX in two ways that matter here: the
  Duplicates tab is now where Summarize lives (so a viewer beside the list is more load-bearing than
  when this plan was written), and the stale banner no longer holds a button - it is plain text, so it
  can stay at the top of the left column without competing with the split.
- A SYNTHETIC demo record exists for screenshots: `SYNTHETIC-demo-record.pdf`, document
  `8572efee-59ba-4bea-b3ef-4d122bb72b99` on the local stack - a generated 9-page PDF with three
  identical "Work Status Report" copies (one kept, two excluded) and two summaries, so both tabs have
  content and the PDF pane has real pages to show. Never screenshot a real record.
- Layout CSS: `.rce-viewer` + `.pdf-pane` (`frontend/app/evaluators-ds.css:847-851`,
  `app/globals.css:145-176`), `.ev-split*` (`evaluators-ds.css:762-777`, stacks below 900px),
  `#step-summaries { flex: 1; overflow: auto; }` (`evaluators-ds.css:216`), `.sum-column`
  (`:217-220`, centered `max-width: var(--content-cap)` with its own padding), the 900px media query
  at `:852-855`.
- Tests: `duplicates-view.test.tsx`, `summaries-view.test.tsx`, `summaries-view.header.test.tsx`
  (all mock their hooks). jsdom does not load the pdf.js iframe; mock `./pdf-viewer` in the specs to
  capture `jumpTo` calls.

## Tasks (implementation blueprint)

### Task 1 - jumpTo works on repeat clicks
- what: MODIFY `frontend/components/review/pdf-viewer.tsx` `useImperativeHandle`'s `jumpTo`: when
  `viewerApp()` reports `pdfViewer.pagesCount`, set `lastPage.current = page`, apply `app.page` and
  update `pageInfo` unconditionally; keep the `page === lastPage.current` early-return only for the
  fallback branch that reassigns `frame.src`.
- pattern: the current handle body at `pdf-viewer.tsx:68-85`.
- approach: test-after
- acceptance (EARS):
  - WHEN `jumpTo(n)` is called twice with the same `n` and the viewer is ready, THE SYSTEM SHALL set
    the viewer page both times.
  - WHEN `jumpTo(n)` is called before the viewer is ready and `n` is unchanged, THE SYSTEM SHALL NOT
    reload the iframe.

### Task 2 - split layout CSS
- what: MODIFY `frontend/app/evaluators-ds.css`: change `#step-summaries` to
  `{ flex: 1; min-height: 0; }` (the inner column now owns the scroll) and ADD, next to the existing
  `.rce-*` rules:
  `.rce-split { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 14px var(--gutter) 20px; }`,
  `.rce-splitcol { flex: 1; min-height: 0; overflow: auto; padding-right: 14px; }`,
  `.rce-split .sum-column { max-width: none; margin: 0; padding: 0 0 12px; }`,
  `.dupe-copy.clickable { cursor: pointer; }`,
  `.dupe-copy.selected, .summary-card.selected { border-color: var(--blue-500); box-shadow: var(--shadow-focus); }`,
  `.row-jump { background: none; border: 0; padding: 0; font: inherit; color: inherit; text-align: left; cursor: pointer; }`;
  and inside the existing `@media (max-width: 900px)` block add
  `.rce-split { overflow: auto; } .rce-splitcol { overflow: visible; }` so the stacked layout scrolls
  as one page.
- pattern: the `.rce-editor` / `.rce-table` / `.rce-viewer` block at `evaluators-ds.css:833-855`.
- approach: code
- acceptance (EARS):
  - WHERE a tab uses `.rce-split`, THE SYSTEM SHALL keep the PDF pane fixed while the list column
    scrolls.
  - WHILE the viewport is at most 900px wide, THE SYSTEM SHALL stack the list above the viewer and
    scroll the whole tab.

### Task 3 - Duplicates: viewer + clickable copies
- what: MODIFY `frontend/components/review/duplicates-view.tsx`: accept `filename?: string`; add
  `const pdfRef = useRef<PdfViewerHandle>(null)` and `const [selectedIdx, setSelectedIdx] = useState<number | null>(null)`;
  add `function openRow(row: DuplicateRow) { setSelectedIdx(row.idx); pdfRef.current?.jumpTo(row.pages.start); }`;
  change the wrapper to `<section id="step-duplicates" className="rce-split">` containing
  `<SplitPane storageKey="mrr.duplicates.split" left={<div className="rce-splitcol">{existing header/banner/list}</div>} right={<div className="rce-viewer"><PdfViewer ref={pdfRef} documentId={documentId} filename={filename} /></div>} />`;
  pass `onOpen` + `selectedIdx` into `ClusterCard`, whose `<li>` gains
  `className={cn("dupe-copy", "clickable", row.primary && "primary", row.idx === selectedIdx && "selected")}`
  and `onClick={() => onOpen(row)}`, whose title span becomes
  `<button type="button" className="row-jump dupe-copy-title" onClick={() => onOpen(row)}>`, and
  whose "Keep this one" handler becomes `(e) => { e.stopPropagation(); onKeep(row.idx); }`.
- pattern: `review-editor.tsx:38,53-56,205-232`; existing card markup at `duplicates-view.tsx:142-164`.
- approach: test-after
- acceptance (EARS):
  - WHEN the reviewer clicks a duplicate copy row, THE SYSTEM SHALL jump the PDF pane to that copy's
    first page and mark the row selected.
  - WHEN the reviewer activates a copy's title button by keyboard, THE SYSTEM SHALL perform the same
    jump.
  - WHEN the reviewer clicks "Keep this one", THE SYSTEM SHALL resolve the cluster and SHALL NOT
    also treat the click as a row jump.

### Task 4 - Summaries: viewer + clickable cards
- what: MODIFY `frontend/components/review/summaries-view.tsx`: accept `filename?: string`; add
  `pdfRef` + `selectedIdx` as in Task 3 with
  `function openSummary(item: SummaryItem) { setSelectedIdx(item.idx); pdfRef.current?.jumpTo(item.row.start); }`;
  wrap the existing `.sum-column` (header, list, pager) in
  `<SplitPane storageKey="mrr.summaries.split" left={<div className="rce-splitcol">...</div>} right={<div className="rce-viewer"><PdfViewer ... /></div>} />`
  inside `<section id="step-summaries" className="rce-split">`, leaving `ExportDialog` outside the
  SplitPane; on the READ-ONLY card add `onClick={() => openSummary(item)}` plus
  `selectedIdx === item.idx && "selected"`, wrap the heading in
  `<button type="button" className="row-jump" onClick={() => openSummary(item)}>`, and add
  `e.stopPropagation()` to the Re-draft, Edit and "In export" handlers. The editing card gets no
  click handler.
- pattern: Task 3 + the existing card markup at `summaries-view.tsx:221-285`.
- approach: test-after
- acceptance (EARS):
  - WHEN the reviewer clicks a summary card, THE SYSTEM SHALL jump the PDF pane to that summary's
    first source page and mark the card selected.
  - WHEN the reviewer clicks Re-draft, Edit or the "In export" checkbox, THE SYSTEM SHALL perform
    only that action.
  - WHILE a card is in edit mode, THE SYSTEM SHALL NOT jump when the reviewer clicks inside it.

### Task 5 - pass the filename down
- what: MODIFY `frontend/components/review/review-page-client.tsx` to pass
  `filename={wf.filename}` to both `<DuplicatesView>` (:244) and `<SummariesView>` (:246-252).
- pattern: the existing `filename={wf.filename}` on `ReviewEditor` (`review-page-client.tsx:233`).
- approach: code
- acceptance (EARS): WHEN a record is open, THE SYSTEM SHALL show the record's filename in the PDF
  pane header on all three tabs.

### Task 6 - tests
- what: EXTEND `frontend/components/review/duplicates-view.test.tsx` and
  `frontend/components/review/summaries-view.test.tsx`: `vi.mock("@/components/review/pdf-viewer", ...)`
  with a stub exposing a captured `jumpTo`, then assert the jump page after a row/card click, after a
  title-button keyboard activation, and that an action button click does not jump.
- pattern: the existing `vi.mock` + `render` style at the head of `duplicates-view.test.tsx`.
- approach: test-after
- acceptance (EARS): The system shall pass the full frontend suite with new-code coverage >= 80%.

## Validation loop

1. FE: `pnpm -C frontend typecheck`
2. FE: `pnpm -C frontend exec vitest run` (full suite)
3. Live (local :8080) with Playwright MCP, full-page screenshots: Duplicates tab shows the list left
   + PDF right; clicking a copy moves the pane to its first page and highlights the row; Summaries
   tab does the same per card; the split handle drags and the position survives a reload; at 900px
   wide the panes stack.

## Risk / rollback

- Blast radius: the Duplicates and Summaries tab bodies, `PdfViewer.jumpTo`, and shared CSS
  (`#step-summaries` loses `overflow: auto`; `.sum-column` is overridden only inside `.rce-split`).
  No backend change.
- Cost: switching tabs remounts the viewer iframe (one conditional 304 for the PDF).
- Rollback: revert the PR (frontend-only, no data).
