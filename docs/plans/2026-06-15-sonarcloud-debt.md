---
status: in-progress
pr: F
branch: fix/sonarcloud-security-and-dup
approach: code (security fixes + behavior-preserving refactor, guarded by the PR D test suite)
---

# PR F - Clear SonarCloud legacy debt to green main

## Goal

Make main's SonarCloud quality gate pass by fixing the pre-existing inherited issues the
first full-codebase analysis flagged: 9 path-traversal/reflected-data vulnerabilities,
70.5% duplication in export.py, and 1 CSRF hotspot. The PR D suite (70 tests, 93%) is the
safety net - re-run after every change; behavior must not change.

## Failing gate conditions (from the SonarCloud API, branch=main)

- Security Rating on New Code = E (9 vulnerabilities) -> need A.
- Duplicated Lines on New Code = 10.2% (export.py 70.5%) -> need < 3%.
- Security Hotspots Reviewed < 100% (1 unreviewed CSRF hotspot) -> need 100%.
- (Passing already: bugs 0, coverage 93%, maintainability A.)

## Fixes (planned together; small codebase)

### 1. Path-traversal / reflected data (9 vulns) - sanitize at the source
All stem from `os.path.join(base, <user filename/folder>)`. Sanitize every user-controlled
path component with `werkzeug.utils.secure_filename` at the boundary:
- `services/files.py`: add `safe_name(name)` helper wrapping `secure_filename` with an
  empty-result fallback (secure_filename can return "").
- `blueprints/upload.py`: `/upload`, `/uploadAndCheckCSV`, `/uploadPages` - sanitize
  `file.filename` before join and before deriving `state.main_filename`. (Clears the
  downstream `files.py` taint finding too, since `txt_filepath` becomes safe.)
- `blueprints/individual_mrr.py`: `/create_patient_folder` (folder_name), `/upload_files`
  (folder_name + each file.filename). Keep `state.patientNameGlobal` RAW (display name, not
  a path).
- `blueprints/export.py`: `/exportResultsToWordFileIndivRecords` - sanitize `patientName`
  used in the output filename.
- `blueprints/reports.py`: `/compute_page_ranges` - sanitize `folder_name` (same vuln class,
  harden for consistency).

Tests use simple names (`case.pdf`, `a.pdf`, `Pat`, `case`) on which secure_filename is a
no-op, so the suite stays green.

### 2. Duplication - extract shared Word builder (export.py)
`exportresultstoword` and `exportResultsToWordFileIndivRecords` share ~100 identical lines
(header/title/MRR section/intro/body-from-state.all_data/conclusion). Extract a private
`_build_mrr_document(patient_name, patient_dob, qme_or_ame, lawfirm) -> Document`. Each route
calls it, then keeps its own save-path + send_file logic (the only real difference). Drops
export.py from 70.5% to ~0, overall < 3%.

### 3. CSRF hotspot - review, not "fix"
The hotspot is a REVIEW item, not a bug. Adding Flask-WTF `CSRFProtect` now would break the
existing tokenless JS frontend (every POST -> 400) and proper CSRF belongs with the future
auth work. Decision: mark the hotspot REVIEWED / ACKNOWLEDGED via the SonarCloud API with a
justification (internal pre-MVP tool, no auth yet; CSRF to land with auth). Satisfies the
"hotspots reviewed = 100%" condition without breaking the app.
**(If you'd rather wire CSRFProtect now and accept the frontend changes, say so.)**

### 4. app.py debug (not gated, but real) - optional hardening
`app.run(..., debug=True)` is dangerous (Werkzeug debugger RCE). app.py is excluded from
analysis (`sonar.sources=mrr_ai`) so it does not affect the gate, but flip it to read
`FLASK_DEBUG` env (default False). Low risk; documents safe defaults before PR E adds a prod
server.

## Verification

- `uv run pytest --cov=mrr_ai` stays green (70 tests) and >= 85%.
- `uv run ruff check . && ruff format --check .` clean; `pre-commit run --all-files` green.
- Push PR -> all checks green incl. SonarCloud (new-code gate).
- After merge -> main full-codebase gate green (security A, duplication < 3%, hotspots 100%).

## Risk / rollback

- Blast radius: file-path handling + export refactor. Behavior-preserving; covered by tests.
- secure_filename changes saved filenames for inputs containing spaces/unicode (e.g.
  "Pat Synthetic" -> "Pat_Synthetic"); acceptable and safer. No CSV-contract change.
- Rollback: revert the PR; gate returns to failing (debt) state.
