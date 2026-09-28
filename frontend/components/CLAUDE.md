# frontend/components - agent instructions

Rules for every component folder. Each subfolder has its own `CLAUDE.md` with the traps specific to it.

## Rules

- Mark a component `"use client"` when it uses hooks, state, effects or browser APIs.
- Never call `fetch` in a component. Reads and writes go through a hook in `frontend/hooks/` or a
  function in `frontend/lib/`; files go through `downloadFile()` in `frontend/lib/download.ts`.
- Show every failure with `humanizeError(err, { fallback, notFound })` from `frontend/lib/errors.ts`.
  Never render `err.message`: it can be the synthesized `"<path> failed (500)"` or `"network"`, and
  `humanizeError` is what maps 0, 401, 403 and 404 to readable sentences. It keeps a server sentence
  only from an `ApiError`, so throw `ApiError`, not a plain `Error`, when a message must survive.
- Style with the design-system classes (`ev-btn`, `ev-inp`, `hd-*`, `rc-*` ...) and tokens from
  `frontend/app/evaluators-ds.css`. App buttons use `ev-btn`, not the shadcn `Button`, which is only
  used inside `components/ui` dialogs.
- Hover hints are native `title` attributes. There is no Tooltip component, only the provider.
- Keep one signal per meaning: red `banner` for a result not to trust or an action that cannot be taken,
  blue `banner-info` for a fact or next step, amber `notice-attention` for "needs attention".
- Confirmations: My documents uses the Radix `ConfirmDialog` (`documents/confirm-dialog.tsx`); the
  workbench, the duplicates view, the summaries view and the prompt dialog use `window.confirm`.
- Every interactive element is a real `<button>`, `<a>` or form control. Do not put click handlers on
  `<li>` or card `<div>`s; see `row-jump` in `review/`.
- UI copy is plain and neutral. ASCII only in new code, copy and comments.
- Public repo: no real names, dates of birth or record content in examples, placeholders or tests.

## Testing components

- Render with Testing Library and `userEvent`. Mock hooks or `@/lib/*` modules with `vi.mock` at the
  top of the file.
- A component that uses TanStack Query needs a `QueryClientProvider` with a fresh `QueryClient`
  (`retry: false`) per test.
- Stub `window.confirm` with `vi.spyOn(window, "confirm")` and restore it
  (`afterEach(() => vi.restoreAllMocks())`). Files share one jsdom worker, so an unrestored spy leaks
  into later files.
- Turn fake timers off in the same file (`vi.useRealTimers()`); `vitest.setup.ts` fails a file that
  leaves them on.

## Commands

Run from `frontend/`:

```bash
pnpm typecheck
pnpm exec vitest run components
pnpm test
```

## Docs

- `docs/explanation/frontend-workbench.md`
- `docs/reference/design-system.md`
- `docs/how-to/extend-the-frontend.md`
