---
feature: summaries-modern-ui
date: 2026-07-06
status: approved
base-branch: feat/user-accounts-multidoc
related-issues: []
---

## Goal

Recreate the settled `Summaries.dc.html` design (Evaluators Design System) in the
existing Flask/Jinja + vanilla-JS review page: new shared app shell (navy top bar +
numbered stepper), redesigned summary cards in a 900px column, and an Export-to-Word
modal dialog replacing the inline export toolbar.

## Context

- Design handoff at `P:\MRR_AI_Source\design_handoff_mrr_modern_ui` (README + 4 pages).
  Only Summaries is being implemented now; Login / My documents / Review & correct come
  later, page by page. The classic UI is untouched.
- The `.dc.html` files are references, NOT production code: the task is to port the
  tokens and rebuild the components as plain HTML/CSS/JS in `mrr_ai/templates/review.html`,
  `mrr_ai/static/review.js`, and app CSS -- no build step, no SPA framework.
- The design MCP could not be authorized in this session, so the token values come from
  the handoff README (complete: colors, type, spacing, radius, shadows, focus ring) and
  the crest comes from the Evaluators website repo
  (`W:\evaluators-website\public\evaluators-crest.png`, 635x664 PNG, 221 KB).
- All wiring already exists: `GET /api/documents/:id/summaries`, `PUT /summaries/:idx`,
  `POST /export` (keys `patientName`/`patientdob`/`QMEorAME`/`lawfirm`). The dialog's
  prefilled evaluation type "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)" matches the
  legacy classic-UI autofill (verified in `templates/index.html:212`). Zero server-side
  changes.

## Approach

**Additive stylesheet, not a token swap.** New file `mrr_ai/static/evaluators.css`
(Evaluators tokens + shell + shared controls + summaries styles), loaded by
`review.html` AFTER `review.css`. The old `review.css` keeps styling the not-yet-redesigned
parts (start/progress panels, editor step, home page, auth pages). Rationale: swapping
the `:root` tokens globally would half-restyle three pages whose reworks are not settled
yet; page-by-page delivery per the README needs page-scoped styles. The old summaries
CSS block in `review.css` is deleted in the same change (used only by review.html --
verified `summary-card`/`chips` classes appear nowhere else; the base `.chip` class used
by home.html stays).

**Shell scope.** The top bar + stepper live in `review.html`, which hosts all three
steps -- so steps 1-2 get the new shell with their OLD content styling until the
Review & correct rework lands. Intended interim state. Home/login keep the old top bar
until their pages are done.

**Alternatives rejected:**
- Global token replacement in `review.css` -- restyles unsettled pages sight-unseen.
- Copying the `.dc.html` verbatim as a template -- prototype markup (inline styles,
  `x-dc` runtime) is explicitly not production code per the README.
- New `summaries.html` template/route -- the summaries view is a step of the existing
  document-scoped SPA; splitting it would duplicate boot/status/polling logic.

**Display glyphs vs ASCII rule:** the design uses middots, en/em dashes in UI copy
("44 summaries — 1 excluded", "pages 1–4", meta separators). Source files stay ASCII by
emitting them as escapes (`·`, `–`, `—` in JS; `&#183;` etc. in HTML).

**Fonts (decided 2026-07-06):** vendor Poppins 600/700 + Inter 400/500/600 woff2 at
`mrr_ai/static/vendor/fonts/` with a local `@font-face` sheet (LAN app, no runtime
third-party dependency, same precedent as vendored PDF.js). Fallback stack:
`"Segoe UI", system-ui, sans-serif`. Branch decision: work continues on
`feat/user-accounts-multidoc` (Adrian's call, same exchange).

**Flagged while touching:** `review.js` is 771 lines (threshold 400). This rework keeps
it one file (no build step; a second script file would need careful load-order and
shared-state plumbing). Splitting the summaries view into its own JS file is proposed as
a separate follow-up, not smuggled into this change.

## Tasks

- T1: Foundation -- crest asset, fonts, `evaluators.css` tokens + controls
  - approach: code
  - files-touched: [mrr_ai/static/assets/evaluators-crest.png (new),
    mrr_ai/static/vendor/fonts/* (new, if vendored), mrr_ai/static/evaluators.css (new),
    mrr_ai/templates/review.html (link tag)]
  - detail: copy crest (downscale to ~112px wide via Pillow if available, else serve
    as-is); token `:root` block exactly per README (navy/gold/blue/gray scales, semantic
    pairs, `--ink`, `--color-text-on-navy #DDE3F0`, `--radius-md 8 / -lg 12 / -pill 999`,
    `--shadow-focus` color-mix ring, `--font-heading Poppins / --font-body Inter`);
    button classes (primary/outline/ghost + small), input/label/checkbox/chip styles
    lifted from the prototype's token-driven CSS; global link colors; press
    `translateY(1px)`; `prefers-reduced-motion` guard.
  - acceptance: review.html loads both sheets; tokens resolve (spot-check computed
    `--navy-600 = #32416C`); fonts render Poppins headings / Inter body.

- T2: Shared shell -- navy top bar + numbered stepper in review.html
  - approach: code
  - files-touched: [mrr_ai/templates/review.html, mrr_ai/static/review.js,
    mrr_ai/static/evaluators.css]
  - detail: top bar 58px navy: white 38px chip + crest, `EVALUATORS` wordmark
    (Poppins 600 13px, .16em), 1px navy-400 divider, "Medical Record Review", right nav
    My documents / Classic UI / **Log out** (`/logout`, new on this page). Stepper: white
    strip, 26px circles + labels, 56px connectors; done = green circle + white check SVG,
    active = navy circle + number, upcoming = gray-100 circle. `show()` in review.js maps
    position vs active step to done/active/upcoming; clicks still call `gotoStep`; busy
    state keeps navigation blocked (reduced opacity).
  - acceptance: all three steps render correct stepper states; step clicks navigate
    exactly as before; Log out signs out.

- T3: Summaries step redesign -- 900px column, cards, single bottom pager, empty state
  - approach: code
  - files-touched: [mrr_ai/templates/review.html, mrr_ai/static/review.js,
    mrr_ai/static/evaluators.css, mrr_ai/static/review.css (delete old summaries block)]
  - detail: header row = H1 "Summaries" (22px/600) + count line
    "N summaries — M excluded from export" + primary "Export to Word" button with
    download SVG (opens dialog, T4). Cards per design: head (16px/600 title, chips
    needs-review/edited/excluded WITH Lucide triangle/pencil SVGs, ghost Edit, Exclude
    checkbox), meta line `date · pages X–Y · category · DOI`
    (13px gray-500), body 14.5px/1.65; excluded card = dashed border + .6 opacity.
    Edit-in-place keeps current fields/behavior, restyled with token inputs. Pager:
    bottom only, centered -- "Page X of Y · a–b of total" + Prev/Next outline
    buttons (top pager removed; save-state indicator moves beside the count line).
    Empty state: document icon, "No summaries yet", "Go to Review & correct" button.
  - acceptance: on the seeded ~100-summary case, cards match the design (chips, meta,
    full text, no inner scrolling), pager pages at 20 with correct range text, empty
    state shows on an unsummarized document.

- T4: Export dialog -- modal replaces the inline export bar
  - approach: code
  - files-touched: [mrr_ai/templates/review.html, mrr_ai/static/review.js,
    mrr_ai/static/evaluators.css]
  - detail: fixed backdrop `rgba(27,37,67,.45)`, 460px white card, close X; subcopy with
    live counts ("These details fill the report header. N summaries will be exported;
    M excluded. Enter only what the report requires — no additional PHI."). Fields:
    Patient name (flex 2) + DOB (flex 1) row; Evaluation type prefilled
    "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)"; Attorney law firm. Footer: ghost
    Cancel + primary "Export N summaries". Opens from the header button; closes on
    X / Cancel / backdrop click / Escape; focus lands on Patient name. Confirm posts the
    existing `/export` payload, disables while running, downloads the blob; errors show
    inline in the dialog (danger text), not on the page banner. Field values persist for
    the session (dialog is not reset on close) so a reopen keeps typed values.
  - acceptance: dialog opens/closes per spec; export downloads a .docx whose header
    carries the four values; excluded summaries stay out (existing server behavior).

- T5: Verification + suite
  - approach: code
  - files-touched: []
  - detail: full suite (163) + ruff + `node --check review.js`; live browser pass on the
    seeded demo account (port 5010) per the Verification section below.
  - acceptance: suite green; every Verification item checked with evidence
    (screenshots + DOM/computed-style checks, full-page not just top viewport).

## Risk / Rollback

- Blast radius: review.html page only (all three steps get the new shell; summaries step
  fully restyled). Home, login, classic UI, all APIs, DB, export format: untouched.
  Zero server-side code changes.
- Interim inconsistency by design: steps 1-2 = new shell + old content styling; home/auth
  keep the old top bar until their page reworks.
- Crest asset is 221 KB (one-time cached; downscaled copy planned).
- Rollback: `git revert` the UI commits; no schema/data implications.

## Phase 2 (added 2026-07-07, Adrian's instruction): complete the entire rework

Decisions (same exchange): (1) strip "(Pages X-Y)" and "[Diagnostic Study]" from
displayed titles, recompose at export from row_start/row_end/row_category (same
machinery as [ManualCheck]/DOI); (2) upload does NOT auto-start identification -
the previous home.js auto-chain is REMOVED (no accidental Vertex spend; deviates
from the mock's "identification starts automatically" copy, which is dropped);
(3) registration password checklist (8+ chars / number / symbol) enforced BOTH
client-side and server-side via a PasswordUtil subclass (password_util_cls,
verified against installed Flask-Security 5.8 source).

- P2-T1: title decorations round-trip -- approach: tdd
  - files: mrr_ai/blueprints/documents_api.py (_export_entry),
    mrr_ai/static/review.js (parseDisplay), tests/unit/test_summary_editing.py
  - acceptance: docx titles carry [ManualCheck]/[Diagnostic Study]/(Pages X-Y)
    exactly as the legacy format regardless of clean web edits; no doubling.
- P2-T2: Review & correct page (settled design) -- approach: code
  - files: mrr_ai/templates/review.html (#step-editor + panels),
    mrr_ai/static/review.js (renderTable/save-state), mrr_ai/static/evaluators.css
  - detail: 1320px column; filename subhead; H1 "N documents / M pages" + green
    Saved check; gold Apply-merges, outline Insert, primary Summarize; row actions
    move to the TITLE row (per design); category select with CSS chevron; gap rows
    warning-tinted dashed; excluded rows dim except the Summarize cell; fixed
    360px PDF viewer; start/progress panels restyled on tokens.
- P2-T3: My documents page (option 1b) -- approach: code
  - files: mrr_ai/templates/home.html, mrr_ai/static/home.js,
    mrr_ai/static/evaluators.css, mrr_ai/blueprints/documents_api.py (rows_count
    via one grouped count query), mrr_ai/models.py (no change unless needed)
  - detail: status filter chips with counts + filename search + sortable headers +
    dot badges (running counts) + overflow menu (Open / Re-run identification /
    Delete...) + 20/page pager + first-run empty state with drop zone and 3-step
    explainer; row click opens the review; upload lands as "Uploaded" (manual
    start decision); poll while jobs active.
- P2-T4: auth pages (option 1a) + password rules -- approach: tdd (rules) + code (UI)
  - files: mrr_ai/templates/security/{base,login_user,register_user}.html,
    mrr_ai/static/register.js, mrr_ai/security.py, tests/unit/test_auth.py,
    tests/conftest.py (compliant test password)
  - detail: navy bar (wordmark only) + 400px elevated card + crest + gold eyebrow;
    danger banner on failed sign-in + field-level errors; live checklist gates the
    submit; MrrPasswordUtil appends number/symbol rules to the stock length check.
- P2-T5: retire review.css + PDF.js console-noise investigation -- approach: code
  - files: mrr_ai/static/evaluators.css, delete mrr_ai/static/review.css,
    template link tags; static/vendor/pdfjs/web/viewer.html (only if the blocked
    inline script turns out to be vendoring damage, not stock)
  - acceptance: no template references review.css; suite green; ruff clean;
    node --check clean on all JS. Live browser verification deferred until
    Adrian approves (his explicit gate).

## Verification

Phase 1 (done 2026-07-06). Phase 2 verification is suite-level only until Adrian
approves live testing: pytest (incl. new docx + password tests), ruff,
node --check on review.js/home.js/register.js, plus Flask test-client GETs of
/login, /register, and / to prove the rebuilt templates render.

On the live dev server (seeded real cases, adriang@gesco.com):
1. Stepper: open a done document -> Summaries active, steps 1-2 green-check; click
   Review & correct -> step 2 navy; click Identify -> re-run warning panel intact.
2. Summaries: cards show chips (needs review / edited / excluded states all present in
   seeded data), meta line format, full body text with no inner scroll; Edit-in-place
   save + cancel; Exclude toggle updates count line and card style.
3. Pager: ~100-summary case -> "Page 1 of 5 · 1–20 of ~100"; Prev disabled on
   page 1; Next walks pages; edit state resets across pages.
4. Dialog: open, Escape/backdrop/X/Cancel close paths; export with the four fields
   downloads a .docx; open it and confirm header values + excluded summary absent.
5. Shell: Log out works; Classic UI link intact; focus rings visible on tab-through;
   `prefers-reduced-motion` disables transitions.
6. Full-page screenshots at ~1440px and ~1024px widths (no overflow/clipping).
7. Suite 163 green, ruff clean, node --check clean.
