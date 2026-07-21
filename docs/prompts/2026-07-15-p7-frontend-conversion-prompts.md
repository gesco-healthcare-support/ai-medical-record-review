# P7 UI design briefs (for Claude Design)

Claude Design (claude.ai/design) is conversational: you give an opening brief, it builds a first
version on the canvas, and you refine by chatting, commenting, or editing. Each `## Screen` below is
**the opening brief for one Claude Design conversation.**

How to use:
1. Prepend the **Product brief** to the screen brief.
2. Paste into a new Claude Design project with our codebase attached (done), so it can read/capture
   the current screen and reuse the design system it built from our code.
3. Refine on the canvas.
4. **Design all screens first.** The handoff to Claude Code happens ONCE, at the very end - see
   "Final step" at the bottom. Do not hand off per screen.

Only the current modern flow is in scope; the old single-user "classic" screens are not.

---

## Product brief (prepend to every screen)

MRR AI is an internal tool for medical evaluators who review scanned workers'-compensation medical
records. The core loop: a user uploads a record, the app splits it into sub-documents and drafts a
summary of each, the evaluator corrects those, and exports a Word report. The users are busy,
non-technical clinicians who work in this all day - the feel should be a calm, dense-but-legible,
professional medical workbench, not a flashy consumer app.

We are redesigning the current app (the modern screens in the attached codebase, `mrr_ai/templates`
+ `mrr_ai/static`, or capture them from the running app) one screen at a time. For each screen:
**capture or read the current version, keep its flow and labels** (staff already know them), and
re-render it in our design system - modernizing layout, spacing, hierarchy, and interactions. Do not
invent a new brand; use the system you built from our code. Design with **reusable, consistent
components** and real accessible patterns (modal dialogs, dropdown menus, sortable data tables,
toasts), because the whole set will be implemented together later in one pass.

---

## Screen 1 - Sign in and register

The entry screen, shown before the app. A single centered card on a calm branded background, no app
chrome. Trustworthy and uncluttered - this tool handles patient data.

- **Sign in (default view):** our wordmark, a one-line tagline, then Email and Password fields, a
  full-width primary "Sign in" button, and small secondary links "Create account" and "Forgot
  password?". A failed login shows a concise error banner inside the card ("Email or password is
  incorrect") - never a reload or a blank page.
- **Create account:** Full name, Email, and Password, with a live requirements checklist under the
  password field (8+ characters / one number / one symbol) that satisfies item-by-item as they type,
  and an inline error if the email is already registered.
- **Forgot / reset:** a minimal "enter your email -> send reset link" card with a success
  confirmation, plus a "set a new password" card for the reset link.
- Design idle, inline-validation, submitting (button spinner), and error states.

## Screen 2 - My Documents (home)

The screen users land on after signing in: their records, where they start new ones, and where they
watch processing finish. Capture the current home + individual-records screens; keep the flow, make
it a clean modern dashboard.

- **Layout:** a top app bar (wordmark left; an "Upload" action and the user menu right) over a
  full-width content area holding one card with a data table of the user's records.
- **Primary actions:** a prominent "Upload record" button at the card's top-right, with a secondary
  "Upload split records" option beside it (the individual-records case: several pre-split PDFs
  combined into one record).
- **Table columns:** Record name | Pages | Status | Uploaded | row actions. Status is a colored pill
  - grey (uploaded), blue with a thin inline progress bar + stage label (processing), green (done),
  red (error). Rows are clickable to open; on hover show "Open" plus a kebab menu with "Delete".
- **States to design:** an inviting empty first-run state (illustration + a single "Upload your
  first record" call-to-action instead of an empty table); a populated list; a row mid-processing
  with live-looking progress ("Summarizing 4 of 12") that updates on its own; a delete confirm modal
  ("Delete this record? This cannot be undone."); and an error row with a retry affordance.

## Screen 3 - Review editor (the largest, most important screen)

The workbench where an evaluator corrects the app's work on one record. Capture the current review
editor and keep its two-pane structure; modernize it into a focused split view. This screen carries
the product - spend the most craft here.

- **Layout:** a slim header (record name, a back link to My Documents, and the primary actions on
  the right: "Auto-fill header", "Segment", "Summarize"), above a resizable two-pane split - left is
  the PDF viewer (scrollable, page numbers visible), right is the sub-documents table.
- **Sub-documents table (the heart of the screen):** one row per detected sub-document, columns
  Pages (e.g. 3-7) | Category (a dropdown of categories) | Title | Date | Injury date | flags. Show
  a small "merge suggested" chip on rows the app thinks continue the row above, with a one-click
  "Merge" on the chip. A checkbox per row controls inclusion in summarization. Editing any cell
  **saves automatically** with a subtle "Saved" micro-indicator - no Save button. Provide row tools:
  merge with the row above, split a row at a page, and a category filter above the table (a
  multi-select, off by default) to focus on one document type.
- **Running a job:** when Segment or Summarize runs, swap the action buttons for a live progress
  state (a slim progress bar + a stage label like "Summarizing 6 of 14") and disable editing until
  it finishes, then refresh the rows. "Auto-fill header" pulls patient name/DOB/law firm and briefly
  confirms.
- **States to design:** loading; loaded (rows beside the PDF); a job running (progress, controls
  disabled); just-finished (rows refreshed); empty (no rows yet); and error (a friendly inline
  message). Nail the split layout, the table, and the running-job state first - they define the
  screen; merge/split affordances second.

## Screen 4 - Summaries and export

Review and edit the drafted summaries, then export the Word report (a step within, or right after,
the Review editor - match how the current UI presents it).

- **The list:** each summary shows its title, date, and body, with an "edited" badge when the
  evaluator changed it, a toggle to exclude it from the export (excluded ones dim but stay visible),
  and a "manual check" marker on ones flagged for a closer look.
- **Per-summary actions:** edit the title/date/text inline, and "re-draft this one" (regenerates a
  single summary - show a small per-item spinner while it works).
- **Export:** a compact form (patient name, DOB, QME/AME, law firm, prefilled from Auto-fill header)
  with a "Download Word report" button.
- **States to design:** no summaries yet; summaries listed; one re-drafting; editing; exporting; and
  error.

## Screen 5 - Bundles (Diagnostic & Operative, Depositions)

Fast, filtered exports of specific document types from a record. Capture the current two bundle pages
- they are one screen differing only by which document types they gather.

- Pick a record (or use the current one), see its matching sub-documents for the bundle, and offer
  two clear actions: "Download combined PDF" (just those pages merged into one file) and "Summarize
  these" (a filtered Word report of only those documents).
- The same screen serves two entry points: "Diagnostic & Operative" and "Depositions".
- **States to design:** no matching documents (a clear, non-alarming message); matches listed;
  generating (button spinner); and error.

## Screen 6 - Admin console

Admins only: manage the categories and prompts that drive classification and summaries. Capture the
current admin screen.

- **Categories table:** name, description, examples, active/inactive, and whether it has a custom
  prompt. Inline edit; "Add category" (a numeric id, fixed once created); and deactivate (a
  soft-delete - it leaves the pickers elsewhere but stays here with its history).
- **Per-category prompt editor:** show the category's custom prompt versus the default the app would
  otherwise use, with a Save action and a short "updated" confirmation.
- **Reprocess a record:** an action to re-run summaries for a chosen record using the current
  prompts.
- **States to design:** the table; editing a category; editing a prompt; and a saved confirmation.

---

## Handoff (all six screens are designed) - do this once, at the end

### Step A - run this IN Claude Design first (polish + prepare the handoff)

Before we hand this off to code, do two things across ALL six screens of this project: apply the
polish below, then prepare the cleanest possible handoff so the coding agent receives everything
with nothing lost.

Polish (apply consistently to EVERY screen):
1. One consistent system. Make the styling consistent across all screens - the same spacing scale,
   type scale, color tokens, radii, shadows, and component styles everywhere. Any element that
   appears on more than one screen (buttons, inputs, tables, cards, dialogs, status pills, the app
   bar, form fields) must look and behave identically. Consolidate to shared components + tokens and
   remove one-off styling.
2. Fluid, proportional responsiveness. Every screen must adapt to the viewport. As the screen gets
   larger, scale the content AND the horizontal padding/margins up together, proportionally - use
   viewport-relative sizing (e.g. clamp() for container padding and key font sizes) with a generous
   maximum content width, so a wide monitor is filled proportionally. Do NOT leave large empty bands
   on the left and right, and do NOT lock content into a narrow fixed-width column. Degrade
   gracefully to tablet and mobile widths too.
3. Target stack. This will be implemented in Next.js (App Router) + React + TypeScript with shadcn/ui
   (Radix primitives + Tailwind CSS). Structure the design so it maps cleanly onto shadcn components
   and Tailwind tokens: use standard patterns that have direct shadcn/Radix equivalents (Dialog,
   DropdownMenu, Table, Tabs, Select, Toast, Tooltip, Progress, Popover), and express the tokens as
   a coherent Tailwind theme (colors, spacing, radii, fonts).

Then prepare the handoff bundle so the coding agent gets everything:
- Include ALL six screens and ALL their states (loading, empty, error, in-progress, success,
  dialogs, hover/focus), not only the happy path.
- Emit the full design system: the token set (colors, typography scale, spacing scale, radii,
  shadows) and a component inventory that names, for each reusable component, the shadcn/Radix
  component it should become.
- Document the responsive rules explicitly (the fluid-gutter behavior above, the breakpoints, and
  how each screen reflows).
- For each screen, include its layout structure, the components it uses, its states, and its key
  interactions (autosave, merge/split, live job progress, polling, inline validation) as notes the
  implementer can follow.
- Keep the markup semantic and accessible (labeled controls, real headings, visible focus states) so
  the shadcn/Radix implementation inherits it.

Confirm the polish is applied to every screen and the handoff includes all screens + all states +
the full design system before exporting.

### Step B - in the "Export to local coding agent" dialog

- Leave "Download zip instead" UNCHECKED - use the Claude Design connector (MCP import), not a zip.
- Paste the following into "Give the agent more detail on what to implement (optional)". It reaches
  the local coding agent alongside the generated MCP-import prompt; because that agent has our repo,
  it points at repo files instead of restating everything:

```
Implement into the EXISTING repo on the current branch - build the screens in the already-scaffolded
`frontend/` Next.js app (App Router, React 19, TypeScript); do not scaffold a new project. Use
shadcn/ui (Radix + Tailwind) for components and TanStack Query for server state, and turn the
design's tokens into the Tailwind theme.

Wire to our existing FastAPI backend, same-origin, through `frontend/lib/api.ts` (apiFetch) - first
delete its dead XSRF double-submit code (our backend uses a SameSite=Lax session cookie, no CSRF
token). The exact endpoints and data shapes are the source of truth in `backend/app/`
(api/documents.py, api/admin.py, auth/routes.py, schemas/); match them precisely. The design is the
source of truth for UI; the backend is the source of truth for data and behavior.

Keep the design's fluid responsive layout: content and horizontal gutters scale with the viewport
(no large empty bands on wide screens; no narrow fixed column).

Conventions: cookie-session auth (on a 401, redirect to /login); poll the documents list every 2s
while any record has an active job, and poll a record's status every 1s while a job runs; never log
a document's filename or any patient text (PHI); use Radix primitives for modals/menus/tables so
accessibility is built in. Implement all six screens. Done = `pnpm typecheck` and `pnpm build` pass
and each screen works against the running backend.
```

### Step C - give the generated prompt to the local Claude Code agent

Paste the export-generated prompt (the `claude_design` MCP import + "Implement: MRR AI.dc.html") to
the local agent. Note: the agent needs the `claude_design` MCP connector available to import via MCP
(the alternative is the zip, which we are not using). The agent implements the design into `frontend/`
per the Step B detail.
