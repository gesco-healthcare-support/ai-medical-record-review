# frontend/components/admin

The admin console rendered by `/admin`: the category catalogue, the per-category summary prompt
editor, and re-running summaries on a record with the current prompts. Only an admin
(`CurrentUser.is_superuser`) sees the page's content; the backend refuses every `/api/admin/*` call
from anyone else.

| File | What it is |
| --- | --- |
| `admin-view.tsx` | `AdminView`: a notice for a non-admin; the categories table (id, name and description, examples, auto-assign, summarize by default, active, custom or built-in prompt) with Edit, Prompt and Activate/Deactivate; the "Reprocess a record" card listing the admin's own summarized records |
| `category-dialog.tsx` | `CategoryDialog`: add or edit a category. The id is entered only on create; errors show inline and as a toast |
| `prompt-dialog.tsx` | `PromptDialog`: edit a category's custom summary prompt, see the built-in prompt beside it, copy it into the editor, or revert to it |
| `admin-view.test.tsx` | Error toasts, the summarize-default column, the reprocess picker, adding and editing categories |
| `category-dialog.test.tsx` | Error handling, dismissal while saving, the payload sent |
| `prompt-dialog.test.tsx` | Built-in versus custom, revert, reopen showing the server's text, a failed load |

Data comes from `frontend/hooks/use-admin.ts` (queries `["admin", "categories"]` and
`["admin", "prompt", id]`) and `frontend/hooks/use-documents.ts`.

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/admin
```

To see the page, sign in as an admin in the running app and open `/admin`; how to make an account an
admin is in [how to manage users and admins](../../../docs/how-to/manage-users-and-admins.md).

## Documentation

- [How to add or change a category](../../../docs/how-to/add-or-change-a-category.md)
- [How to change a summary prompt or rule](../../../docs/how-to/change-a-summary-prompt-or-rule.md)
- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md)
- [Categorization](../../../docs/explanation/categorization.md)
