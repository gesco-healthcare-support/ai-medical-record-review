# frontend/components

Every React component the pages render, grouped by screen. Almost all of them are client components
that read server state through the hooks in `frontend/hooks/` and call the backend only through
`frontend/lib/`. Each component's tests sit beside it as `<name>.test.tsx`.

| Folder | What it holds |
| --- | --- |
| `admin/` | The admin console at `/admin`: categories table, category dialog, summary prompt dialog, reprocess card |
| `app/` | App chrome shared by signed-in pages: the navy app bar, brand, back link, user menu |
| `auth/` | The `/login` flow: sign in, register, forgot password, reset password, and their shared card |
| `bundle/` | The category bundle builder used by `/diagnostics` and `/depositions` |
| `documents/` | My documents at `/`: upload, split upload, the records table, status pill, confirm dialog |
| `review/` | The record workbench at `/records/[id]`: rows editor, duplicates, summaries, export, PDF viewer |
| `ui/` | Primitives: shadcn/ui wrappers over Radix (dialog, alert dialog, dropdown menu, button), the toaster, the tooltip provider, segmented tabs |

## Use, run and test

Components are imported with the `@/components/...` alias. Tests run in the shared vitest worker:

```bash
cd frontend
pnpm exec vitest run components
```

To run one folder, pass its path, for example `pnpm exec vitest run components/review`.

## Documentation

- [The record workbench](../../docs/explanation/frontend-workbench.md)
- [Frontend routes and data reference](../../docs/reference/frontend-routes-and-data.md)
- [Design system reference](../../docs/reference/design-system.md)
- [How to extend the frontend](../../docs/how-to/extend-the-frontend.md)
