# frontend/components/admin - agent instructions

## Rules

- Admin means `CurrentUser.is_superuser` (the backend's `is_admin` column, exposed through a synonym).
  The page must still render a friendly notice for a non-admin who deep-links; the API is what refuses.
- Category ids are create-only and permanent: they key existing review rows. Never make the id field
  editable in the edit path.
- Every category or prompt mutation must invalidate `["admin", "categories"]`; prompt save and revert
  must ALSO invalidate `["admin", "prompt", id]`, or reopening the dialog shows the pre-save state and
  hides "Revert to built-in". The hooks in `frontend/hooks/use-admin.ts` do this; use them.
- Show save failures BOTH inline and as a toast. The dialogs may be dismissed mid-save (Escape, overlay,
  corner button) on purpose; the toast lives in the root layout and survives the dialog (#264). Do not
  "fix" this by blocking dismissal.
- `PromptDialog` is never unmounted, only hidden. Reset its text in an effect keyed on `open` and the
  query data, or a stale draft reappears on reopen and Save writes it.
- While the prompt is loading or failed to load, keep the textarea and Save disabled and say the prompt
  could not be loaded. An empty editable box reads as "no custom prompt" (#263).
- Deactivating a category that review rows still use is refused by the server with 409; surface the
  server's message.
- Reprocess replaces reviewer edits on that record's summaries. Keep that warning in the copy.

## Traps

- `useDocuments()` (owner-scoped) feeds the reprocess picker, so it lists the admin's own summarized
  records even though the endpoint accepts any owner's record.
- Category ids are strings on the wire (`"3"`, `"100"`). Compare with `String(a) === String(b)`.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/admin hooks/use-admin lib/admin-api
pnpm typecheck
```

## Docs

- `docs/how-to/add-or-change-a-category.md`
- `docs/how-to/change-a-summary-prompt-or-rule.md`
- `docs/reference/frontend-routes-and-data.md`

<!-- reviewed: 2026-09-30 -->
