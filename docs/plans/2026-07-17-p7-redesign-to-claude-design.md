# P7 redesign — align the workbench screens with the Claude Design

status: draft (awaiting Adrian's approval)
branch: feat/nextjs-fastapi-rewrite
approach: code (UI; verify-after with Playwright against the design screenshots)

## Why

The Review editor, Bundles, and Summaries screens were built by mirroring the **Flask**
templates (stepper, `_doc_editor` two-row table + embedded pdf.js viewer, `bundle.html`'s embedded
editor). The Claude Design **redesigned** these screens beyond Flask, so the build reads as "the
Flask app." Confirmed live via Playwright (real Chrome, localhost:8080) on 2026-07-17.

**Corrected rule:** when a screen exists in both Flask and the Claude Design and they differ, the
**Claude Design wins**. Sources of truth: the 5 design screenshots Adrian shared + handoff
`screens.md` / `components.md` / `tokens.md` / `responsive.md` + `_ds` tokens. (`MRR AI.dc.html` is
the canvas; not pulled — screenshots + specs are enough.)

## Already correct — keep as-is

Brand tokens match the DS exactly: navy `#32416C`, gold `#C2A14D`, Poppins/Inter, gray scale,
radii 8/12/16, navy-tinted shadows. AppBar, UserMenu (dropdown nav), the crest, StatusPill tones,
auth, My-documents landing. **This is layout/component work, not a re-theme.**

## Shared components to build / refactor (do these first)

1. **SegmentedTabs** (Radix `Tabs`, pill list on a gray-100 track; active = white bg + shadow-sm +
   navy text). Used by the Review editor (Review & correct | Summaries · N) AND Bundles
   (Diagnostic & Operative | Depositions). NEW.
2. **PdfViewer — rewrite.** Replace the vendored pdf.js iframe (which renders the whole pdf.js app
   chrome: Manage pages / Find / Zoom / Highlight / Draw / Print / Save / Tools) with a **custom
   continuous-scroll viewer**: dark `#525659` well, visible page numbers, "Page N of M" header,
   programmatic **jump-to-page** (row click), virtualized. Likely `react-pdf` (pdfjs-dist) with our
   own thin chrome. HIGHEST-EFFORT item; remove `frontend/public/pdfjs`.
3. **SplitPane** — resizable 2-pane, 6px `role="separator"` col-resize handle (hover navy-300),
   left 24–70% persisted; stacks vertically <900px. Replaces the fixed `.editor-split` flex. NEW.
4. **DataTable (picker/read-only variants)** — a lean records table for the Bundle picker (hover
   Select, no kebab/delete) and a read-only matches table; distinct from the My-documents table.
5. **StatusPill** — add the processing variant's embedded 3px progress bar + live label
   ("Summarizing 4 of 12") per components.md (current pill has dot+label only).

## Per-screen

### A. Review editor `/records/[id]` — biggest rebuild
Current: 3-step **Stepper** (Identify/Review/Summaries) swapping whole panels; header actions "+
Insert document" + "Summarize"; **full pdf.js iframe**; **two-row-per-doc** table; no Injury-date col.
Target (screenshot 1 + screens.md §3):
- Slim white header: `← My documents` · record name + "N documents · M pages" · **SegmentedTabs
  (Review & correct | Summaries · N)** · autosave micro-indicator · right: **Auto-fill header**
  (outline) / **Segment** (outline) / **Summarize N documents** (primary). While a job runs, the
  actions are replaced by a slim progress bar + stage label + %.
- **Drop the stepper.** The editor workbench is always the "Review & correct" tab; "Summaries" is
  the other tab. "Identify/segment" and "summarize" become the header buttons + an inline progress
  state (editing disabled, table 60% opacity), not separate full-screen steps.
- **Resizable SplitPane**: sub-docs table left (58% default), custom PdfViewer right.
- **Single-row sub-docs table**: Pages (start–end numeric) | Category (Select) | Title
  (borderless-until-hover) | Date | **Injury date** | Review ☑ | Summarize ☑ | hover row-tools
  (merge-up / split / delete, icon buttons + tooltips). Keep autosave, validation, gap strips,
  gold merge-suggestion strips, category-filter popover, +Insert.
- **Auto-fill header** button → wire the existing `POST /api/documents/{id}/extract-header` (stores
  patient/DOB/law-firm, prefills export). Currently unbuilt.
Tasks: build SegmentedTabs, SplitPane, PdfViewer; rewrite `rows-table` to single-row + Injury date;
rewrite `review-page-client` (tabs + always-on editor + inline job progress, drop Stepper/Start/
Progress panels); add Auto-fill header; wire Segment button.

### B. Summaries (Review editor, Summaries tab) — closest, needs polish
Current: reached via the stepper's Summaries step; "N summaries"; cards with "Re-run" / "Edit" /
"Exclude"; no badges.
Target (screenshot 2 + screens.md §4):
- Reached via the **Summaries tab**; reading column max **1100px**; count line "N summaries · M
  excluded from export"; **Export to Word** primary.
- **SummaryCard**: title + **badges** (Edited=info, Manual check=gold+flag icon, Excluded=gray) +
  date · pages · category meta; actions **Re-draft** (rename from "Re-run"), **Edit**, **"In
  export"** checkbox (rename/invert from "Exclude"); body 14px/1.65 pre-line; excluded = 55% opacity.
Tasks: refine `summaries-view` (count line, badges, label rename, in-export semantics) + host it in
the Summaries tab.

### C. Bundles `/diagnostics` + `/depositions` — second-biggest rebuild
Current: picker = full My-documents table (chips/search/kebab/extra cols/pagination) + an Upload PDF
control; no on-page tab switch; Stage 2 embeds the **whole review editor** (two-row table + pdf.js
chrome) + a stacked action card.
Target (screenshots 3–5 + screens.md §5):
- **SegmentedTabs (Diagnostic & Operative | Depositions)** top-right; the whole screen re-labels.
- **Stage 1 picker:** lean records table (name / pages / status / uploaded), hover **Select**, **no
  delete, no upload, no kebab, no filter chips**. Unidentified record → "This record hasn't been
  identified yet" panel (Choose another / Open in Review & correct).
- **Stage 2:** breadcrumb (`← Choose another record` · name · "Fix categories in Review & correct →")
  + **LEFT** card "N matching documents" — **read-only** table (Pages | Title | Category | Date) +
  **RIGHT** aside card (4 header fields prefilled from Auto-fill + **Download combined PDF** [outline,
  no fields required] + **Summarize to Word** [primary], each with its own spinner + inline
  success/error line). **No embedded editor, no PDF viewer.**
- Empty matches → "That's normal — not every record has them…" dashed card (non-alarming).
Tasks: rewrite `bundle-page-client` — remove the embedded ReviewEditor + upload; add SegmentedTabs,
the lean picker, the breadcrumb, the read-only matches table, and the aside form; keep the existing
`bundle-api` download calls.

### D. Admin `/admin` — align to spec (no design screenshot yet)
Current: table ID | Name | Auto-assign | Active | Summary prompt | Edit/Prompt/Deactivate; reprocess
= free-text record-ID input; prompt dialog wide + mono.
Target (screens.md §6 + components.md — NO screenshot provided):
- Table: ID | **Name & description** (stacked) | **Examples** (·-joined, muted) | Auto-assign | Active
  pill | Summary-prompt pill (Custom=info / General=gray) | Edit / Prompt / Deactivate. Inactive rows
  55% opacity.
- Prompt dialog (720px): if custom, a gray **reference panel** "General prompt this category would
  otherwise use" (mono, scrollable) + "Revert editor to this"; footer note about when it applies.
- **Reprocess:** a **Select of Summarized records** + "Re-run summaries" (spinner → inline confirm),
  NOT a free-text UUID input.
Tasks: adjust `admin-view` (Name&description + Examples columns, inactive dim), `prompt-dialog`
(reference panel + revert), `admin-view` reprocess (Select instead of text input).
OPEN: no design screenshot for Admin — build from spec, then Adrian confirms (or shares a shot).

## Cross-cutting

- **Fluid layout** (responsive.md): shared container `px-[clamp(20px,4vw,96px)] mx-auto` with
  max-widths — data screens (My docs / Bundles / Admin) **1840px**, Summaries reading column
  **1100px**, onboarding **980px**. Fluid titles `clamp(24px,20px+0.6vw,34px)`.
- Responsive: Bundles matches+aside stack <1024; Review editor split stacks <900 (table over PDF,
  divider hidden, row-click still jumps); app bar drops the name text <640.
- Keep using the DS tokens already in place; prefer Radix/shadcn primitives per components.md where
  we're already rewriting a component. NOT in scope: a wholesale swap of every existing primitive
  (ev-btn/ev-inp) that already renders on-brand — the "looks like Flask" problem is structural, not
  the base primitives.

## Out of scope / untouched
Auth, My documents landing (already match), backend/APIs (all endpoints exist), brand tokens.

## Build order (Adrian: plan all four, then build)
Shared components (SegmentedTabs, PdfViewer, SplitPane) → **A. Review editor** → **C. Bundles** →
**B. Summaries polish** → **D. Admin**. Commit + push per screen; Playwright-verify each against the
design screenshot before moving on.

## Verification
Per screen: `pnpm typecheck` + `pnpm build`; Playwright (real Chrome) screenshot vs the design
screenshot; check computed styles for the DS token values. Live AI paths need the ADC kept fresh
(user-impersonation reauth gotcha).

## Open items
- Admin has no design screenshot (build-from-spec + confirm).
- PdfViewer library choice (react-pdf vs a hand-rolled pdfjs-dist canvas renderer) — decide at the
  PdfViewer task; both drop the vendored full-viewer chrome.
