# frontend/hooks - agent instructions

## Query rules

- One stable key per resource. Export a key function when two places use it (`summariesKey()`,
  `duplicatesKey()`); never retype the array.
- Poll only while work is running: `refetchInterval: (query) => (running ? 2000 : false)`.
- Every mutation invalidates every key its write can change, or patches the cache with the item the
  server returns. Prompt writes invalidate BOTH `["admin", "categories"]` and `["admin", "prompt", id]`.
- Do not add `refetchOnWindowFocus` or shorten `staleTime` to paper over a missing invalidation; the
  defaults in `frontend/app/providers.tsx` apply app-wide.
- `useCurrentUser()` must not retry: a 401 means signed out.

## `use-review-workflow.ts` invariants

- `keySeq` in `frontend/lib/review-rows.ts` is module-global and NEVER reset. Tests that want fixed keys
  build `EditorRow`s directly.
- Replace the row buffer wholesale only through `replaceRows()`; it clears the touched set.
- The touched set tracks only `SERVER_WRITABLE_FIELDS` (`include`, `category`). A new server-writable
  field must be added there AND copied in `applyServerRowChanges()`, which names the two explicitly.
- `reloadRows()` merges server fields into unsaved work; it must never flush local rows first (that
  overwrites the server write that triggered it).
- The boot cleanup FLUSHES the pending save to the effect's own `documentId`; it must not just clear the
  timer. Every stand-down goes through `takePendingSave()` (timer AND pending ref). `onSummarize()`
  stands the save down because `startSummarize()` sends the rows itself.
- Async flows and the cleanup read refs (`rowsRef`, `saveStateRef`, `totalPagesRef`, `activeJobRef`),
  not state: their closures predate boot. Keep each ref updated wherever its state is set.
- `pollJob()` is single-flight (`clearPoll()` first). Name every `JobState` in it: an unnamed state
  keeps polling forever. `cancelled` resolves; it is not an error.
- `watchSummarize()` invalidates `["summaries", id]` before branching on done, needs_attention and
  cancelled (a rejected poll skips it). `Summary.idx`
  is positional; a stale list lets an edit overwrite a new summary.
- `needs_attention` reads the attention payload's message first, then `job.error`.
- The Stop escalation lives in the page, keyed on `activeJobId`; keep `setActiveJobId` updated on every
  poll.
- `activeStep`, `gotoStep` and the `enableSummaries` option have no production consumer (the `Stepper`
  is not rendered, and the bundle pages fetch the record themselves despite the docstring).

## `use-download-watch.ts`

- The server, not the page, knows how a download went. Poll `GET <url>/status` every `POLL_MS`; keep
  watching `interrupted` (the browser's Resume can finish it); stop on `complete`, `expired`, a 404,
  `WATCH_LIMIT_MS`, a null argument or unmount. A failed status request is not an answer: keep the
  last sentence and ask again.
- Never put the file name in a log or a sentence; it carries the patient's name.

## Tests

- Mock the `lib` module with `vi.mock("@/lib/<file>", () => ({ ... }))` above the imports.
- Build a fresh `QueryClient` with `retry: false` per test; spy on `invalidateQueries` to assert keys.
- For the 800 ms debounce and the 1 s poll, use fake timers inside `act` and switch back to real
  timers in the same test; `frontend/vitest.setup.ts` fails a file that leaves them on.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run hooks
pnpm typecheck
```

## Docs

- `docs/explanation/frontend-workbench.md` (autosave, polling, reloads, Stop, downloads)
- `docs/reference/frontend-routes-and-data.md` (query keys and polling table)

<!-- reviewed: 2026-09-30 -->
