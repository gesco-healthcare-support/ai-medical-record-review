# frontend/hooks

React hooks that own server state and timing: TanStack Query queries and mutations over the clients in
`frontend/lib/`, the download watcher, and `useReviewWorkflow`, the state machine behind the record
workbench. Components read and write the backend only through these hooks or the `lib` functions.

| File | What it is |
| --- | --- |
| `use-review-workflow.ts` | `useReviewWorkflow(documentId)`: boot from the stored record, watch a segment or summarize run (1 s status poll), the row buffer with 800 ms autosave, the touched set, stop and restart, `reloadRows()` after other tabs write |
| `use-documents.ts` | `useDocuments()` (`["documents"]`, polls every 2 s while any record has an active job), `useUploadDocument()`, `useAggregateDocuments()`, `useDeleteDocument()`, `useStartIdentification()` |
| `use-duplicates.ts` | `duplicatesKey()`, `useDuplicates()` (polls every 2 s while the dedup job is queued or running), `useResolveDuplicate()`, `useStartDedup()` |
| `use-summaries.ts` | `summariesKey()`, `useSummaries()`, `useSaveSummary()`, `useResummarize()`; saves patch the cached list in place |
| `use-admin.ts` | `useCategories()`, `useCreateCategory()`, `useUpdateCategory()`, `useSavePrompt()`, `useRevertPrompt()`, `useReprocess()` |
| `use-auth.ts` | `useLogin()` (refreshes `["current-user"]`), `useRegister()`, `useForgotPassword()`, `useResetPassword()` |
| `use-current-user.ts` | `useCurrentUser()`: `GET /api/users/me`, no retry, 5 minute stale time |
| `use-download-watch.ts` | `useDownloadWatch(prepared)`: asks the server every 2 s how a handed-over download is going and returns one sentence, a tone and whether it is still watching |
| `use-review-workflow.test.tsx` | Boot decisions, polling outcomes, autosave, flush on leave, stop and restart, summaries invalidation |
| `use-review-workflow.touched-keys.test.tsx` | Switching documents drops the previous document's touched fields; an unsaved edit on the current document stays protected |
| `use-documents.test.tsx`, `use-duplicates.test.tsx`, `use-summaries.test.tsx`, `use-admin.test.tsx`, `use-auth.test.tsx`, `use-current-user.test.tsx` | Polling conditions, the endpoint each mutation calls, invalidation and cache patches |
| `use-download-watch.test.tsx` | Each download state, the 30 s "not started" sentence, recovery after an interruption, a failed status request, the 15 minute limit, letting go |

Query defaults (30 s stale time, no refetch on focus, one retry except 401, 403 and 404, the 401
redirect) come from `frontend/app/providers.tsx`.

## Use, run and test

```bash
cd frontend
pnpm exec vitest run hooks
```

Hook tests render with `renderHook` inside a `QueryClientProvider` built per test, and mock the `lib`
module the hook calls.

## Documentation

- [The record workbench](../../docs/explanation/frontend-workbench.md)
- [Frontend routes and data reference](../../docs/reference/frontend-routes-and-data.md) (query keys and polling)
- [How to extend the frontend](../../docs/how-to/extend-the-frontend.md)
