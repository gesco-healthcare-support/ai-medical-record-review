---
feature: My Documents clickable-row affordance
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Make My Documents rows visibly clickable: restore the (currently dead) pointer cursor + hover
tint, and add a subtle "open" chevron that fades in on row hover.

## Context & decisions
- Backlog item 6 of 7 (MRR AI rewrite; W:\mrr-ai on main). Pure frontend, visual-only.
- FINDING (Chesterton's Fence): the cursor + hover CSS ALREADY exists but is DEAD -
  `evaluators-ds.css:378-379` target `.hd-table tbody tr[data-id]`, but the React table renders
  `<tr>` WITHOUT a `data-id` (`documents-table.tsx:175`), so neither rule matches. The Flask app
  (same rules in `mrr_ai/static/evaluators.css`) works because its template emits `<tr data-id=...>`.
  So this is a port regression, not missing CSS.
- Decision: fix by adding `data-id={doc.id}` to the `<tr>` (restores the existing Flask-faithful
  cursor + gray-50 tint) rather than rewriting the selector, because it matches the source and
  touches no shared CSS behavior.
- Decision: the "open cue" is NEW (the Flask app has no chevron); implement it by mirroring the
  existing hover-reveal pattern at `evaluators-ds.css:832-834` (`.bnd-selectcell .ev-btn` opacity
  0 -> 1 on `tr:hover`), because reusing an established pattern keeps it consistent and low-risk.
- Scope note: `DocumentsTable` is shared, so the bundle picker rows get the same affordance too
  (desirable - they are clickable). The kebab-menu cell already stopPropagation (`:189`), so the
  cue/row click never fires when using the menu.

## All needed context
- `frontend/components/documents/documents-table.tsx:175` - `<tr key={doc.id} onClick={() => onOpen(doc.id)}>`
  (add data-id + the cue). Imports lucide icons at `:4` (ArrowDown/ArrowUp/FileText) - add ChevronRight.
- Name cell `:176-181` - `<td><span className="hd-doc"><FileText/><span className="hd-name">{name}</span></span></td>`.
- `evaluators-ds.css:378-379` - the dead `tr[data-id]` cursor + `:hover { background: var(--gray-50) }` rules.
- `evaluators-ds.css:832-834` - the hover-reveal pattern to mirror for the cue (opacity 0 -> 1, transition .12s).
- `evaluators-ds.css:381` - `.hd-doc { display:flex; align-items:center; gap:10px; color:var(--gray-400) }`.

## Tasks
1. MODIFY frontend/components/documents/documents-table.tsx
   - what: add `data-id={doc.id}` to the `<tr>` at `:175`; import `ChevronRight` from lucide-react at `:4`;
     render `<ChevronRight width={15} height={15} aria-hidden className="hd-open-cue" />` as the trailing
     element inside the `hd-doc` name span (after `hd-name`).
   - pattern: icon usage mirrors `FileText` at `:178`; hover-reveal mirrors `.bnd-selectcell` at
     `evaluators-ds.css:832-834`.
   - approach: code
   - acceptance (EARS): WHEN a document row is rendered, THE SYSTEM SHALL set a `data-id` attribute
     equal to the document id on its `<tr>`.
2. MODIFY frontend/app/evaluators-ds.css
   - what: add `.hd-open-cue { opacity: 0; color: var(--gray-400); transition: opacity .12s; }`
     and `.hd-table tbody tr[data-id]:hover .hd-open-cue { opacity: 1; }`. Leave `:378-379` as-is
     (task 1 activates them by adding the attribute).
   - pattern: mirror `.bnd-selectcell` reveal at `:832-834`.
   - approach: code
   - acceptance (EARS): WHILE the pointer is over a document row, THE SYSTEM SHALL show the pointer
     cursor, a gray-50 row background, and the open-cue chevron at full opacity; otherwise the chevron
     SHALL be hidden (opacity 0).

## Validation loop
- `cd frontend && pnpm typecheck` (clean) + `pnpm build` reaches "Compiled successfully" + type/lint
  + static-gen. NOTE (Windows): `pnpm build` cannot finish the `output:"standalone"` symlink copy on
  Windows (EPERM - needs elevated/dev-mode); that step is Docker/CI-only. On Windows, "Compiled
  successfully + typecheck clean" IS the pass; the standalone build is proven in Docker/CI.
- Preview My Documents with >=1 document: screenshot at rest (no chevron, no tint) and on row hover
  (pointer cursor + gray-50 tint + chevron visible); confirm clicking the kebab menu does NOT open
  the record. (Final chevron placement - inline-after-name vs far-right of row - confirmed from the
  hover screenshot; acceptance is the behavior, not the pixel position.)

## Risk / rollback
- Blast radius: the shared `DocumentsTable` (My Documents + bundle picker rows). Visual only; no
  data, logic, schema, or API change.
- Rollback: `git revert` the commit (two files); nothing stateful to undo.
