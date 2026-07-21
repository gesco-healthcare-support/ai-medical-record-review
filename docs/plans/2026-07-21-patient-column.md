---
feature: Patient name column in My Documents
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Add a sortable, searchable "Patient" column to the My Documents table, right after Document, showing
the extracted patient name (item 1).

## Context & decisions
- Backlog item 2 of 7. Depends on item 1 (listing() now returns patient_name). Pure frontend.
- Placement: new column immediately after Document (Adrian). Sortable + folded into the search box.
  Empty -> em-dash (matches the existing empty cells; written ASCII-safe as "—"). Label "Patient".

## All needed context
- documents-table.tsx: SORT_ACCESSORS :31, COLUMNS :40, search filter :88, name <td> (ends with the
  hd-open-cue chevron), "no rows" colSpan :171 (7 -> 8 columns now).
- DocumentListItem.patient_name added in item 1 (lib/types.ts).
- evaluators-ds.css hd-w-* column widths near :368.

## Tasks (approach=code)
1. MODIFY documents-table.tsx - add `patient` to SORT_ACCESSORS (patient_name lower); add
   {key:"patient",label:"Patient",cls:"hd-w-patient"} after Document in COLUMNS; extend the search
   filter to also match patient_name; add a <td>{doc.patient_name || "—"}</td> after the name
   cell; bump the empty-state colSpan 7 -> 8.
   Acceptance (EARS): WHEN My Documents renders, THE SYSTEM SHALL show a Patient column after Document
   with the record's patient name (or an em-dash when unknown); WHEN the user sorts by it or types in
   the search box, THE SYSTEM SHALL sort/filter by patient name.
2. MODIFY evaluators-ds.css - .hd-table th.hd-w-patient { width: 200px; }.

## Validation loop
- cd frontend && pnpm typecheck (clean). End verify: column present, sort + search work on a record
  whose header was extracted.

## Risk / rollback
- Blast radius: My Documents table (+ bundle picker, shared). Display only. Rollback: git revert.
