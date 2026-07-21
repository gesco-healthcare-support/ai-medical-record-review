---
feature: Render summary markdown (bold/italic) on the page and in the Word export
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Render the summarizer's inline markdown (**bold**, and *italic*/_italic_) as real emphasis on the
Summaries page AND in the Word export so no raw markers leak; the edit box keeps raw markdown.

## Context & decisions
- Backlog item 3 of 7. Adrian: render (not strip) the emitted markers; edit box shows raw.
- Sampled 689 real stored summaries: ~2955 **bold** pairs; italics/headings/bullets/backticks
  effectively absent (1-3 each). Bold dominates -> a tiny shared inline parser (bold + italic)
  covers reality + the "and so on" without a heavy markdown dependency.
- Today: summaries-view.tsx:263 renders {text} literally; reporting.py:92 dumps
  `_{date}_\t****{title}****: {text}` as one plain run (literal _ and ****). Decision: one parser
  shape mirrored in TS (MarkdownText) + Python (reporting._add_inline_runs) so the page and the
  .docx agree; date -> italic run, title -> bold run (drop the literal _/**** wrappers). Structural
  tags ([ManualCheck], (Pages...), [Diagnostic Study], DOI) are literal text and stay untouched.

## Tasks
1. CREATE frontend/components/review/markdown-text.tsx - MarkdownText renders **bold** / *italic* /
   _italic_ -> <strong>/<em>, plain otherwise. approach: test-after. Acceptance (EARS): WHEN text
   contains **x**, THE SYSTEM SHALL render x bold with no asterisks shown.
2. MODIFY summaries-view.tsx - render the card title + body via MarkdownText (edit textarea stays raw).
   approach: code. Acceptance: WHEN a summary with **x** displays, THE SYSTEM SHALL show x bold and
   no literal `**`.
3. MODIFY reporting.py - add _run + _add_inline_runs (parse ** / * / _ into bold/italic runs); rebuild
   the per-entry body as runs (date italic, title bold, body parsed). approach: code. Acceptance:
   WHEN the .docx is built from a summary with **x**, THE SYSTEM SHALL emit x as a bold run and no
   literal `**` / `_` / `****`.

## Validation loop
- cd frontend && pnpm typecheck; backend py_compile reporting.py. End verify: a summary with **bold**
  shows bold on the page and in the exported .docx; the edit box shows the raw `**`.

## Risk / rollback
- Blast radius: summaries display + the Word/bundle export body formatting (both build_mrr_document).
  Rollback: git revert; delete markdown-text.tsx.
