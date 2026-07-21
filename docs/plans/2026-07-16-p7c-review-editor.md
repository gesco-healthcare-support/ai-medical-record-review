# P7c - Review editor (and P7d summaries) plan

Status: in progress. Route: `/records/[id]`. Faithful reproduction of the Flask
`review.html` + `_doc_editor.html` + `review.js` using the DS classes in `evaluators-ds.css`
(`.ev-stepper`, `.center-panel`, `.bar`, `.rc-*`, `#rowsTable`, `.editor-split`, `.sum-*`,
`.summary-card`, `.ev-dialog*`, `.ev-chip*`).

## Scope split

- **P7c (this phase):** the page shell + 3-step `ev-stepper` + boot/step-routing + banner,
  `step-start` (identify panel), `step-progress` (job poll every 1s), and the full
  `step-editor` (Review & correct workbench). `step-summaries` renders **read-only** (list +
  cards + pager + empty) so the pipeline is visible end-to-end.
- **P7d (next):** make summaries interactive - edit-in-place, re-draft, exclude toggle, and
  the Export-to-Word dialog. (Decide then whether to add "Auto-fill header": the Flask editor
  has no such button, so it is out of scope by fidelity unless Adrian wants it.)

## Backend contract (authoritative in backend/app/api/documents.py)

- `GET /api/documents/{id}` -> `{ ...listing, rows:[...], categories:[{id,name}] }` (boot).
- `GET /api/documents/{id}/status` -> `{ status, job:{kind,state,stage,current,total,error}|null }`
  (poll every 1s while a job runs).
- `PUT /api/documents/{id}/rows` `{ rows:[...] }` (autosave; debounce 800ms; only when valid).
- `POST /api/documents/{id}/segment/start` (identify; 409 if a job runs).
- `POST /api/documents/{id}/summarize/start` `{ rows:[...] }` (flush rows + start; needs >=1 included).
- `GET /api/documents/{id}/pdf` (viewer source; owner-checked; range requests).
- P7d: `GET /summaries`, `PUT /summaries/{idx}`, `POST /summaries/{idx}/resummarize`, `POST /export`.

Row shape: `{start,end,category,title,date,injury_date,flag,suggest_merge,include}`.

## PDF viewer (decision)

Vendor the Flask app's pdf.js viewer (`mrr_ai/static/vendor/pdfjs`, 8.2 MB) into
`frontend/public/pdfjs/` and use it in an `<iframe>`, exactly as the Flask app does. Chrome's
built-in PDF viewer ignores `#page` changes after first load, so a programmatic page API is
required for row-click jump-to-page (a behavior screens.md says not to lose). Jump via
`iframe.contentWindow.PDFViewerApplication.page = N` (same-origin). `?file=` points at the
same-origin `/api/documents/{id}/pdf` (proxied to the backend in dev; the session cookie rides
along). Rejected react-pdf: more setup + I'd rebuild the continuous-scroll/page-jump the
vendored viewer already provides.

## State machine (ported from review.js)

Boot `GET /{id}`: active segment job -> watch (progress, 1s poll) -> editor; active summarize
job -> watch -> summaries; status done -> summaries; rows present -> editor; else -> start
panel. Stepper steps (identify/review/summaries) are positional (before active = done/green,
active = active, after = upcoming) and disabled while a job is watching. Free navigation
between steps via the stepper (blocked while watching).

## Files

- `frontend/public/pdfjs/**` (vendored, cp -r).
- `lib/review-api.ts` (getDocument, saveRows, startSegment, startSummarize, getStatus; P7d adds
  summaries/export). `lib/types.ts` += `Row`, `DocumentDetail`, `CategoryOption`.
- `hooks/use-document.ts` (the document detail query) + a small poll helper.
- `components/review/` : `stepper.tsx`, `start-panel.tsx`, `progress-panel.tsx`,
  `rows-table.tsx` (autosave + validation + gap strips + merge/split/insert + category select +
  flags/include + row-select), `pdf-viewer.tsx` (iframe + imperative jump-to-page),
  `review-editor.tsx` (the step-editor: header + editor-split), `summaries-view.tsx`
  (read-only in P7c; interactive in P7d), `review-page-client.tsx` (the boot/step orchestrator).
- `app/records/[id]/page.tsx` (server: AppBar + Suspense-wrapped client).

## Key interactions (editor)

Autosave (debounced 800ms, valid states only, "Unsaved changes..." -> "Saved"); client
validation mirroring the server (`1<=start<=end<=pageCount`, no overlap with previous, gaps
allowed) with the first error surfaced in the header and invalid rows tinted; gap strips
between non-contiguous rows; merge-up (+ gold "likely same doc" suggestion chips + "Apply N
suggested merges"); split at page (inline form); insert document (page range, sorts into
place); category `<select>`; Review/Summarize checkboxes; row click selects + jumps the viewer;
"Summarize N documents" (disabled on errors or 0 included) flushes rows + starts the job.

## Verification

`pnpm typecheck` + `pnpm build`; authed render of each step; row-click jumps the viewer;
autosave PUT fires; identify/summarize start + progress poll (verified with a real upload +
Adrian's Vertex ADC for the AI steps). Adrian click-tests the interactions.
