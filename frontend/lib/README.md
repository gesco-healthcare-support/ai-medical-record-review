# frontend/lib

The frontend's plain TypeScript: the same-origin API client and error helpers, one client module per
backend area, the download hand-over, the wire types, and the pure row rules the workbench applies.
Nothing here renders; hooks and components import these functions.

| File | What it is |
| --- | --- |
| `api.ts` | `apiFetch()` (prefixes `/api`, sends the session cookie, JSON by default), `ApiError`, `signedOut()` (redirect to `/login` and return the error to throw), `errorFromResponse()` (reads a string `detail` or `error`, else `"<path> failed (<status>)"`) |
| `errors.ts` | `humanizeError()` (the one mapping from an error to a sentence), `isOffline()`, `lacksServerMessage()` |
| `auth-api.ts` | `login()` (form-encoded), `logout()`, `register()`, `forgotPassword()`, `resetPassword()` |
| `documents-api.ts` | `listDocuments()`, `uploadDocument()`, `aggregateDocuments()`, `deleteDocument()`, `startIdentification()` |
| `review-api.ts` | The workbench calls: record detail, status, duplicates, dedup start, cancel, resolve, rows, segment and summarize start, header extract and save, summaries; the `HeaderFields` and `DuplicateAction` types |
| `admin-api.ts` | Categories list, create and update; prompt get, put and delete; reprocess; the `AdminCategory`, `PromptInfo` and `CategoryInput` types |
| `bundle-api.ts` | `BundleConfig`, `DIAGNOSTIC_OPERATIVE`, `DEPOSITIONS`, `BUNDLES` (the only copy of the bundle taxonomy), `downloadBundlePdf()`, `downloadBundleSummary()` |
| `download.ts` | `downloadFile()` (POST, then a native browser download of the prepared file), `fetchDownloadStatus()`, `PreparedDownload`, `DownloadState`, `DOWNLOAD_INTERRUPTED`, `DOWNLOAD_NOT_PREPARED` |
| `review-rows.ts` | Row keys (`withKeys()`, `newKey()`, `stripKeys()`), `sortRows()`, `mergeRows()`, `applyServerRowChanges()`, `SERVER_WRITABLE_FIELDS`, `touchKey()`, `touchedFields()`, `couldNotIdentify()`, `categoryWasGuessed()`, `rowErrors()`, `clearFlagOnEdit()`, `moveSharedBoundary()` |
| `summary-order.ts` | The Summaries tab's search and order: `findSummaries()`, `orderSummaries()`, `summaryDateValue()`, `SummaryOrder` |
| `types.ts` | Wire types: `CurrentUser`, `JobKind`, `JobState`, `JobProgress`, `DocumentStatus`, `DocumentListItem`, `DocumentDetail`, `Row`, `CategoryOption`, `SummaryItem`, `VerifyIssue`, duplicate types |
| `utils.ts` | `cn()`: `clsx` plus `tailwind-merge` |
| `api.test.ts`, `errors.test.ts`, `auth-api.test.ts`, `documents-api.test.ts`, `review-api.test.ts`, `admin-api.test.ts`, `bundle-api.test.ts`, `download.test.ts` | URL, method and body of each call; error mapping; the download hand-over and its failures |
| `review-rows.test.ts` | Every row rule, case by case |
| `summary-order.test.ts` | The search, date order and date reading of the Summaries tab |
| `review-rows.property.test.ts` | Invariants of the row rules over generated rows (`@fast-check/vitest`) |

## Use, run and test

Import with the alias, for example `import { apiFetch } from "@/lib/api"`. Tests stub `fetch` with
`vi.stubGlobal` and assert what was sent:

```bash
cd frontend
pnpm exec vitest run lib
```

## Documentation

- [Frontend routes and data reference](../../docs/reference/frontend-routes-and-data.md) (every call, wire types, error mapping)
- [The record workbench](../../docs/explanation/frontend-workbench.md) (row rules, downloads)
- [HTTP API reference](../../docs/reference/http-api.md)
