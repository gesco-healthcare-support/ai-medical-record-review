# Frontend / UI, and readiness for a backend/frontend split

> Explanation doc. Endpoint contract: [../reference/api-routes.md](../reference/api-routes.md);
> backend pipeline: [segmentation.md](segmentation.md), [categorization.md](categorization.md),
> [summarization.md](summarization.md).

This documents how the UI is built **today** and what it would take to move the frontend into a
**separate repository**, leaving this repo as the backend (a JSON HTTP API + the AI pipeline).
The short version: the UI is almost entirely decoupled from the server already, so a split is
mostly about the **API boundary** (origin, auth, and per-session state), not about untangling
templates.

## How the UI works today

Flask serves **both** the UI and the API from one app:

- **UI pages** are 8 Jinja templates rendered by GET routes in
  [../../mrr_ai/blueprints/pages.py](../../mrr_ai/blueprints/pages.py). Each is a self-contained
  HTML page with inline `<script>` logic; there is **no build step** and no SPA framework.
- **Client libraries** load from CDNs: **jQuery 3.6.0**, **select2 4.0.13** (enhanced
  dropdowns), and **Google Fonts**. The only local assets are
  [../../mrr_ai/static/styles.css](../../mrr_ai/static/styles.css) (~430 lines) and a favicon.
- **Data flow** is entirely client-side `fetch`/jQuery AJAX from the page JS to the JSON
  endpoints (`/upload`, `/getPages`, `/summarize`, `/exportresultsto*`, etc.).

### The pages

| Route | Template | What it drives |
|-------|----------|----------------|
| `/` | `index.html` | Main workflow hub: upload -> segment -> review CSV -> summarize -> export |
| `/pages` | `pages.html` | Automatic page-segmenter (`/getPages`) UI |
| `/pagesManual` | `pagesManual.html` | Manual page-map entry UI |
| `/pdfsegment` | `pdfsegment.html` | Split a PDF into 100-page files (`/segmentPDF`) |
| `/checkCSV` | `checkCSV.html` | CSV validation (`/uploadAndCheckCSV`) UI |
| `/DiagAndOpReports` | `DiagAndOpReports.html` | Extract/merge category 3/8 pages (`/getDiagOpRep`) |
| `/DepositionReports` | `DepositionReports.html` | Extract category 9 pages (`/getDepoRep`) |
| `/IndividualMRR` | `individual_mrr.html` | Patient-folder, multi-file individual-record workflow |

`/reset` (POST) clears the shared server state between runs.

## Why a clean split is feasible

The coupling between these templates and the server is unusually thin:

- **No server-rendered data.** The only Jinja in any template is
  `url_for('static', filename=...)` for the stylesheet and favicon - two directives per page.
  No page bakes in server-side variables, so the HTML is effectively a **static shell**. The
  templates could be served as plain files with the two `url_for` calls replaced by static
  asset paths.
- **The API is already JSON over HTTP.** Almost every POST route returns JSON (or a file
  download for exports). The frontend already treats the server as an API, not as a page
  renderer. The contract is enumerated in
  [../reference/api-routes.md](../reference/api-routes.md).

So a split is "lift the templates/CSS/JS into a frontend repo (or rebuild them as an Angular/
React app) and point them at the backend's base URL." It is **not** a rewrite of data-bound
templates.

## What blocks a clean split (must address)

These are the real coupling points - all on the API boundary, not in the markup:

1. **Same-origin assumption.** The page JS calls **relative** paths (`/upload`, ...). Split
   across origins, the frontend needs a configurable **API base URL**, and the backend needs
   **CORS** enabled for the frontend's origin (uploads are `multipart/form-data`; exports are
   `send_file` downloads - both work cross-origin once CORS allows them).
2. **No authentication or session.** There is **no login, session, cookie, CSRF, or auth** in
   the app (the "Steps to login" doc refers to host/VM access, not app auth). Today any client
   that can reach the server can drive it. A split frontend - especially one on the public web
   handling PHI - requires real **authn/authz** at the API boundary, and then **CSRF/token**
   handling.
3. **Global single-process state is not per-user.** The backend carries the workflow across
   requests via module globals in [../../mrr_ai/state.py](../../mrr_ai/state.py)
   (`pdf_filepath`, `all_data`, ...), shared by **all** clients. One browser's upload overwrites
   another's. Before a real (multi-client) frontend, this must become a **per-session store**
   (already tracked in [../decisions/0003-modularization.md](../decisions/0003-modularization.md)
   / architecture "State model"). This is the single biggest blocker.
4. **Stateful request ordering.** The API assumes a sequence (`/upload` -> `/getPages` ->
   `/summarize` -> export) with state carried server-side. A decoupled frontend either keeps
   that contract (per session) or the backend moves to passing identifiers/state explicitly per
   call.

## A target split shape

- **This repo = backend only:** the `mrr_ai` package minus `templates/`, `static/`, and
  `blueprints/pages.py` (the GET render routes). It exposes the documented JSON API + the AI
  pipeline. Add CORS + auth + a per-session state store.
- **New frontend repo:** the HTML/CSS/JS (lifted as-is, or rebuilt as an Angular/React SPA),
  configured with the backend base URL, handling auth and PHI display.
- **The contract between them** is exactly [../reference/api-routes.md](../reference/api-routes.md)
  plus the [CSV contract](../reference/csv-contract.md).

## Split-readiness checklist

- [ ] Replace `url_for('static', ...)` with static asset references (or a bundler).
- [ ] Introduce a configurable API base URL in the client (no hardcoded relative paths).
- [ ] Enable CORS on the backend for the frontend origin.
- [ ] Add authentication + authorization at the API boundary; then CSRF/token handling.
- [ ] Replace global `state.py` with a per-session store (multi-client safety) - the gating item.
- [ ] Decide framework: lift-and-shift the jQuery pages, or rebuild as a modern SPA.
- [ ] Confirm PHI handling across the new origin boundary (transport security, auth, logging).

## Known frontend issues (flagged)

- **Dead client reference:** the page JS calls `/addtomrr`, but **no such route exists** in any
  blueprint. It is a leftover from the pre-refactor app; either wire a route or remove the call
  when the frontend is reworked.
- **CDN dependence:** jQuery/select2/fonts load from third-party CDNs at runtime - an
  availability and (for PHI tooling) supply-chain consideration; vendor them in the split.

## Related

- API surface (the split contract): [../reference/api-routes.md](../reference/api-routes.md)
- State model + single-process constraint: [../architecture.md](../architecture.md)
- Backend stages: [segmentation.md](segmentation.md), [categorization.md](categorization.md),
  [summarization.md](summarization.md)
