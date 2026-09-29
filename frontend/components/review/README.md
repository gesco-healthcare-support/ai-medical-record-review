# frontend/components/review

The record workbench at `/records/[id]`: three tabs (Review & correct, Duplicates, Summaries) beside the
record's PDF, the report header, the export dialog, and the start and progress panels. The lifecycle
(boot, job polling, autosave, stop and restart) lives in `frontend/hooks/use-review-workflow.ts`; the
row rules live in `frontend/lib/review-rows.ts`.

| File | What it is |
| --- | --- |
| `review-page-client.tsx` | `ReviewPageClient`: the header bar (back link, record name and counts, tabs, per-tab actions, progress bar with two-stage Stop), the banners (including the note an admin sees on another reviewer's record), the duplicate-check gate on Summarize, and the tab body |
| `review-editor.tsx` | `ReviewEditor`: the Review & correct toolbar (Insert document, Apply suggested merges, Could not identify filter, first validation error) over a `SplitPane` of `RowsTable` and `PdfViewer`; merge, split, insert and delete |
| `rows-table.tsx` | `RowsTable` and `categoryOptions()`: two table rows per document (title line with chips and actions, fields line with start, end, category, date, injury date, review flag and summarize), gap strips for skipped pages |
| `header-bar.tsx` | `HeaderBar`: the nine report-header fields, Auto-fill or Re-detect, and Save |
| `duplicates-view.tsx` | `DuplicatesView`: duplicate clusters beside the PDF with keep, remove-one and dismiss actions, and the stale, never-checked, failed and unreadable banners |
| `summaries-view.tsx` | `SummariesView` and `displayTitle()`: summary cards (edit, re-draft, in export, category), chips, the collapsed check findings, paging, and the Export button |
| `export-dialog.tsx` | `ExportDialog`: Word, memo, linked PDF and zip exports, handed to the browser and watched |
| `pdf-viewer.tsx` | `PdfViewer`: the vendored pdf.js viewer in a same-origin iframe, with a `jumpTo(page)` handle and a "Page N of M" header |
| `split-pane.tsx` | `SplitPane`: a resizable two-pane layout whose left width persists in `localStorage` |
| `markdown-text.tsx` | `MarkdownText`: renders `**bold**`, `*italic*` and `_italic_` from summaries |
| `start-panel.tsx` | `StartPanel`: "Ready to identify documents" |
| `progress-panel.tsx` | `ProgressPanel`: the first identification run's progress |
| `stepper.tsx` | `Stepper` and the `StepId` type; the type is used by the workflow hook, the component is rendered only by its test |
| `review-page-client.test.tsx` | Tabs, gate reasons, banners, Stop escalation, restart, confirmations |
| `review-editor.test.tsx` | Merge, split, insert, delete, the boundary and flag rules through `field()` |
| `rows-table.test.tsx`, `rows-table.unidentified.test.tsx`, `rows-table.attention.test.tsx` | Row actions, the Could not identify and Category guessed chips, filtering, attention rows |
| `header-bar.test.tsx` | Save, Auto-fill and Re-detect, the doctor select, the pages-received value |
| `duplicates-view.test.tsx` | Cluster states, resolve actions, banners |
| `summaries-view.test.tsx`, `summaries-view.header.test.tsx` | Cards, chips, edits, re-classify, the header on the Summaries tab |
| `summaries-view.title.test.ts` | `displayTitle()`: each decoration stripped, look-alikes kept, a whitespace-only title returning promptly |
| `export-dialog.test.tsx` | Prefill, the four endpoints, download watching |
| `pdf-viewer.test.tsx`, `split-pane.test.tsx`, `markdown-text.test.tsx`, `start-panel.test.tsx`, `progress-panel.test.tsx`, `stepper.test.tsx` | The smaller components |

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/review hooks/use-review-workflow lib/review-rows
```

To see it, open a record from My documents in the running app. Identification and summarization need
the AI workers, which CI does not start, so these flows are not covered end to end.

## Documentation

- [The record workbench](../../../docs/explanation/frontend-workbench.md)
- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md)
- [How to extend the frontend](../../../docs/how-to/extend-the-frontend.md)
- [Duplicate detection](../../../docs/explanation/duplicate-detection.md)
- [Summarization](../../../docs/explanation/summarization.md)
