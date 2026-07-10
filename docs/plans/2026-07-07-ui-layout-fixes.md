---
feature: ui-layout-fixes
date: 2026-07-07
status: approved
base-branch: feat/user-accounts-multidoc
related-issues: []
---

## Goal

Fix five UI defects Adrian found in live testing: auth card pinned left, missing
Name field on registration, gutter-stranded whitespace on every post-login page, and
an unusably small PDF viewer on Review & correct.

## Context

Live measurements at 1600px viewport (Playwright, this session):
- Auth card: x=20 (left edge). `.auth-main` is a column flex (inherits
  `flex-direction: column` from the base `main` rule) with `align-items: flex-start`,
  so the cross axis (horizontal) pins the card left.
- My documents: `.hd-column` capped at `max-width: 1200px` -> 200px dead gutter each side.
- Review & correct: `.rc-column` capped at `1320px` -> 140px each side.
- Summaries: `.sum-column` capped at `900px` -> ~350px each side.
- PDF viewer: `.viewer-wrap { flex: 1 1 360px; max-width: 360px }` -> hard-pinned at
  360px while the table takes 888px; too small to read.

**Root principle (Adrian's clarification):** the gutters must be SMALL and grow slowly;
the CONTENT must absorb the extra width as the screen grows. Fixed px caps do the
opposite - they freeze content and hand all new width to the margins. This matches the
established preference in memory [[responsive-fluid-gutters]] (clamp/vw gutters + a high
cap, never a low fixed cap that strands whitespace). Applies to ALL four pages,
Summaries included (Adrian declined a special reading cap for it).

## Approach

**One fluid container, shared tokens.** Introduce `--gutter: clamp(20px, 3.5vw, 72px)`
and `--content-cap: 2560px`. Every page column becomes:

```
width: 100%;
max-width: var(--content-cap);
margin: 0 auto;
padding-left: var(--gutter);
padding-right: var(--gutter);
```

Effect by width (gutter = the only side space until the cap):
- 1440px -> ~50px gutters, ~1340px content (was 1200 capped / 120 gutter on home).
- 1600px -> 56px gutters, ~1488px content (was 200px gutter on home).
- 1920px -> 67px gutters, ~1786px content.
- 2560px -> capped at 2560, gutters = 72px padding only (no centering margin yet).
- >2560px -> a centering margin appears; the cap is a safety valve so text lines and
  the table do not become absurd on 4K/ultrawide. Adjustable in one token if Adrian
  wants it higher.

The gutter clamp maxes at 72px, so it never balloons the way the current caps do - the
exact behavior Adrian asked for. `--content-cap` is high enough that no real monitor
below 4K sees a centering margin; content is effectively fluid across the whole normal
range.

**Alternatives rejected:**
- No cap at all: summary paragraph lines and the review table would stretch past
  comfortable limits on 4K/ultrawide; a high safety cap costs nothing on normal screens.
- Per-page different caps: reintroduces the "content frozen, gutter grows" complaint on
  whichever page gets the lower cap. One shared cap is consistent.

**PDF viewer (approved 55/45, proportional):** remove the `max-width: 360px` pin; make
the split proportional so both panes grow with the page, and taller so they use the
viewport height instead of a fixed 640/560:
- `.table-wrap { flex: 55 1 560px }`, `.viewer-wrap { flex: 45 1 440px }` (grow values
  55/45 split the free space in that ratio; basis + min keep them side by side until
  ~1040px, then `flex-wrap` stacks them).
- Both panes: `height: clamp(520px, calc(100vh - 232px), 1000px)` so they fill the
  screen under the topbar+stepper+header instead of a fixed box.
- Result at 1600px: viewer ~640px (was 360); at 1920px ~760px; scales up from there.
  This is proportional at ALL sizes, which was Adrian's real point - not a one-width fix.

**Name field (required):** DB column stays NULLABLE (existing users - adriang - have no
name; a NOT NULL column would fail the boot migration on the seeded DB). "Required" is
enforced by a form validator, so new registrations must provide it while existing
accounts keep working.
- Add `name = db.Column(db.String(255))` to `User`; add `("name","VARCHAR(255)")` under
  a new `"user"` key in `_ADDITIVE_COLUMNS` so the seeded DB upgrades in place.
- Custom `MrrRegisterForm(RegisterFormV2)` adds `name = StringField(validators=
  [DataRequired()])`; wire via `Security(app, datastore, register_form=MrrRegisterForm,
  ...)`. Persistence is automatic: FS `to_dict(only_user=True)` passes form fields that
  match a User column to `create_user` (verified against installed FS 5.8 source).
- Add the Name input to `register_user.html` (first field, above Email).

## Tasks

- T1: fluid container + auth centering -- approach: code
  - files: mrr_ai/static/evaluators.css
  - detail: add `--gutter` / `--content-cap` tokens; rewrite `.hd-column`, `.rc-column`,
    `.sum-column`, and `.sum-header`/pager alignment to the fluid pattern; fix
    `.auth-main { align-items: center }`. Remove the three fixed px caps.
  - acceptance: at 1280/1600/1920 the side gutter equals the clamp value (<=72px), not a
    growing centering margin; auth card horizontally centered.

- T2: PDF viewer proportional + taller -- approach: code
  - files: mrr_ai/static/evaluators.css
  - detail: drop `.viewer-wrap` max-width; set 55/45 flex on table/viewer; both panes
    viewport-height via clamp; keep wrap fallback for narrow screens.
  - acceptance: viewer width grows with the page (>=600px at 1600, larger at 1920);
    table keeps its columns legible; panes stack below ~1040px.

- T3: Name field on registration -- approach: tdd
  - files: mrr_ai/models.py, mrr_ai/__init__.py (_ADDITIVE_COLUMNS),
    mrr_ai/security.py (MrrRegisterForm + wiring), mrr_ai/templates/security/register_user.html,
    tests/unit/test_auth.py
  - detail: nullable `name` column + additive migration; required-on-form; persisted via
    to_dict(only_user); template field with label "Name".
  - acceptance: register with name -> User.name persisted; register without name ->
    form re-rendered, no user created; existing login (adriang) unaffected; boot
    migration adds `name` to the seeded DB without data loss.

- T4: verify -- approach: code
  - files: (none)
  - detail: full suite + ruff + node --check; Playwright measurement sweep of gutters
    and viewer width at 1280 / 1600 / 1920 / 2560; full-page screenshots of all four
    pages. Live manual QA remains Adrian's (he is driving); server left running.
  - acceptance: suite green; measured gutters <=72px through 2560 (then capped);
    screenshots show centered auth card and a large viewer.

## Risk / Rollback

- Blast radius: evaluators.css (all pages) + registration path (model column, form,
  template). No change to documents API, jobs, export, or the summaries/review logic.
- The `name` column is additive and nullable: the boot migration is the same mechanism
  already proven for review_rows/summaries; seeded data is safe.
- Rollback: `git revert` the commits; the nullable column can stay harmlessly or be
  dropped later.

## Verification

Automated (this session): pytest, ruff, node --check; Playwright gutter/viewer
measurements at four widths + screenshots. Then hand back to Adrian for live QA on the
running server (login centered, register Name field + validation, all pages fill the
screen, PDF viewer usable).
