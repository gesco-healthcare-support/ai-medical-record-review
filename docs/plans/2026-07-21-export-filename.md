---
feature: Patient-named export filename
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Name the summaries .docx `Lastname_Firstname_Medical_Records_summary.docx` from the persisted header
(item 1), falling back to `<original-filename>_summary.docx` when no patient name is known.

## Context & decisions
- Backlog item 4 of 7. Depends on item 1 (persisted first/last name). BE + FE.
- Today documents.py:562 hardcodes filename="summaries.docx"; export-dialog.tsx:73 hardcodes
  link.download="summaries.docx" (the blob download name overrides Content-Disposition).
- Decision: compute the name server-side (single source) and have the client read it from the
  Content-Disposition header, so both agree.

## All needed context
- documents.py: os + re already imported (:14-15); _download_name helper :512; export route
  Content-Disposition :562; Document has patient_first_name/patient_last_name (item 1).
- export-dialog.tsx submit() :49-82 - fetch -> blob -> link.download.

## Tasks (approach=code)
1. MODIFY documents.py - add _summary_filename(document): "_".join([last, first, "Medical_Records_
   summary"]) for present name parts, else "<original-stem>_summary"; sanitize to [A-Za-z0-9_.-];
   ".docx". Use it in the export route's Content-Disposition.
   Acceptance (EARS): WHEN a record with patient Doe/Manny exports, THE SYSTEM SHALL set
   Content-Disposition filename="Doe_Manny_Medical_Records_summary.docx"; IF no name is known, THE
   SYSTEM SHALL use "<original-filename>_summary.docx".
2. MODIFY export-dialog.tsx - parse filename from the Content-Disposition response header; use it for
   link.download (fallback "summaries.docx"). Acceptance: WHEN the export downloads, THE SYSTEM SHALL
   save the file under the server-provided filename.

## Validation loop
- cd frontend && pnpm typecheck; backend py_compile. End verify: export a summarized record and
  confirm the downloaded filename.

## Risk / rollback
- Blast radius: summaries export filename only (bundle exports unchanged). Rollback: git revert.
