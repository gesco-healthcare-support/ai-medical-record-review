# frontend/components/bundle

The category bundle builder behind `/diagnostics` and `/depositions`. The reviewer picks an identified
record, sees the documents whose category is in the bundle's set, and downloads either their pages as
one PDF or a Word report summarizing just those documents. One component serves both routes; the
`BundleConfig` passed in (from `frontend/lib/bundle-api.ts`) is the only difference.

| File | What it is |
| --- | --- |
| `bundle-page-client.tsx` | `BundlePageClient`: the record picker (the reviewer's records, newest first), the selected record's pane (`loading`, `failed`, `unidentified` or `ready`), the matched documents table, and the build aside with Auto-fill, patient, DOB, evaluation type, law firm, "Download combined PDF" (or "Download separate PDFs" for a bundle with `separateAs`) and "Summarize to Word" |
| `bundle-page-client.test.tsx` | Error messages, header prefill, duplicate-aware matching, the empty and failed panes, downloads being watched |
| `bundle-page-client.flow.test.tsx` | Picking and switching records, Auto-fill, labels in the matches table, switching bundles with the tabs |

Data: `useDocuments()` for the picker, a `useQuery(["document", id])` for the chosen record,
`extractHeader()` for Auto-fill, and `downloadBundlePdf()` / `downloadBundleSummary()` handed to
`useDownloadWatch()` for the downloads.

## Use, run and test

```bash
cd frontend
pnpm exec vitest run components/bundle
```

To add a bundle, follow [how to extend the frontend](../../../docs/how-to/extend-the-frontend.md)
("Add a category bundle").

## Documentation

- [Frontend routes and data reference](../../../docs/reference/frontend-routes-and-data.md) (Bundles)
- [Exports and downloads](../../../docs/explanation/exports-and-downloads.md)
- [Export formats reference](../../../docs/reference/export-formats.md)
