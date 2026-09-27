# frontend/components/bundle - agent instructions

## Rules

- The bundle taxonomy lives ONLY in `frontend/lib/bundle-api.ts` (`BUNDLES`). The backend has no copy;
  the bundle endpoints and the export zip are handed these values. Never inline a category list here.
- Tabs switch ROUTES (`BUNDLE_TABS` value = config slug, `href` = route). A new bundle needs an entry
  here, a route page and a user-menu item.
- The matches list must mirror `backend/app/services/bundles.py` `matched_rows()`: category in the set,
  minus a non-primary, non-dismissed member of a cluster that HAS a primary. Decide on cluster state
  computed over ALL rows before the category filter; a row alone cannot tell "resolved away" from "not
  resolved yet". Do not filter on `include`.
- Keep the four pane states distinct. A failed record fetch is `failed`, never "not identified yet":
  `detail` is undefined both while loading and after an error.
- "Choose another record" must clear the header fields, the result line and the watched download, so
  one patient's details never carry into the next record.
- Prefill from the stored header only into EMPTY fields; a manual edit or an Auto-fill wins.
- Downloads go through `downloadBundlePdf()` / `downloadBundleSummary()` (which use `downloadFile()`),
  and both buttons stay disabled while a handed-over download is being watched.

## Traps

- `DEFAULT_QME` is duplicated in `review/export-dialog.tsx`, and `categoryLabel` in
  `review/summaries-view.tsx`. Change them together.
- The picker reads `useDocuments()`, which polls every 2 s while any record has an active job.
- The `["document", id]` query is never invalidated; it follows the app-wide 30 s stale time.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run components/bundle lib/bundle-api
pnpm typecheck
```

## Docs

- `docs/reference/frontend-routes-and-data.md` (Bundles)
- `docs/how-to/extend-the-frontend.md` (Add a category bundle)
