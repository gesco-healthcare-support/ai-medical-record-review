---
feature: Nav home-link and reusable BackLink
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Make the crest + "EVALUATORS" wordmark one home link on every signed-in page, and add a single
reusable BackLink ("<- My documents", overridable) to the sub-pages that lack one (admin,
diagnostics, depositions); the review page reuses the same component.

## Context & decisions
- Backlog item 5 of 7 (MRR AI rewrite; W:\mrr-ai). Pure frontend, nav-only.
- FINDING: the review page already has a back link (review-page-client.tsx:70) and the crest links
  home (brand.tsx:14), but /admin, /diagnostics, /depositions have NO back link, and only the crest
  (not the wordmark) is the home link.
- Decision: crest + wordmark become ONE home link via Brand (all pages use the shared AppBar); the
  app descriptor stays plain text - the wordmark is the obvious target, the descriptor is not a link.
- Decision: one reusable BackLink (href default "/", label default "My documents", per-page
  overridable); the review page's inline link is replaced by it - consistency + central management.
- Decision: place BackLink via a padded row (.ev-page-back) in each sub-page's page.tsx, not inside
  the client components - keeps clients untouched and the link gutter-aligned.

## All needed context
- brand.tsx (crest+wordmark; Link imported); AppBar renders <Brand homeLink /> (app-bar.tsx:12).
- evaluators-ds.css: .ev-topbar gap 16px (:99-100); .rce-back style to mirror (:763-768); --gutter.
- app/{admin,diagnostics,depositions}/page.tsx - each is <AppBar/> + a client component.
- review-page-client.tsx:70-72 inline rce-back Link (ArrowLeft + "My documents"); ArrowLeft + Link
  imported at :4-5 and used ONLY there -> drop those imports when replaced.

## Tasks (all approach=code)
1. CREATE components/app/back-link.tsx. Acceptance (EARS): WHEN BackLink renders with no props, THE
   SYSTEM SHALL render a link to "/" reading "My documents".
2. MODIFY components/app/brand.tsx - crest + wordmark in one Link.ev-brand-home when homeLink; plain
   otherwise; app label stays outside. Acceptance: WHILE homeLink is true, THE SYSTEM SHALL make both
   the crest and the wordmark a single link to "/".
3. MODIFY app/evaluators-ds.css - add .ev-brand-home (inline-flex, gap 16, opacity hover), .ev-backlink
   (mirror .rce-back), .ev-page-back (padding: 14px var(--gutter) 0).
4. MODIFY app/{admin,diagnostics,depositions}/page.tsx - render <div class="ev-page-back"><BackLink/></div>
   between AppBar and the client. Acceptance: WHEN a signed-in user views /admin, /diagnostics, or
   /depositions, THE SYSTEM SHALL show a "My documents" back link above the content.
5. MODIFY components/review/review-page-client.tsx - replace the inline rce-back Link with <BackLink/>;
   drop the now-unused Link + ArrowLeft imports. Acceptance: WHEN the review page renders, THE SYSTEM
   SHALL show the shared BackLink (no behavior change).

## Validation loop
- cd frontend && pnpm typecheck (clean). pnpm build compiles (standalone step is Docker/CI-only on Windows).
- End verify (real browser, batch): back link present + working on /admin, /diagnostics, /depositions,
  /records/[id]; crest AND wordmark navigate home from every page.

## Risk / rollback
- Blast radius: shared Brand/AppBar (all signed-in pages) + 3 pages + review page. Visual/nav only.
- Rollback: git revert; delete back-link.tsx.
