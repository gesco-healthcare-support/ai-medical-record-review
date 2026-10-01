# How to extend the frontend

Use this page when you add something to the Next.js frontend: a page, a backend call, a category
bundle, a report-header field, a row field, a summary-card flag, a job stage or status, or new styles.
Each change ends with the tests to add and the checks to run. The reasons behind the rules are in
[the record workbench](../explanation/frontend-workbench.md); the tables you will update are in the
[frontend routes and data reference](../reference/frontend-routes-and-data.md).

## Prerequisites

- Node and pnpm. CI builds with Node 24 and the Docker image with Node 22; there is no `.nvmrc` or
  `engines` pin. pnpm comes from corepack, pinned in `frontend/package.json` (`pnpm@9.15.0`).
- The dependencies installed. From the repository root:

```bash
cd frontend
corepack enable
pnpm install
```

- For end-to-end tests, the app running behind the proxy on port 8080. See
  [how to run the app locally](run-the-app-locally.md).

ESLint checks `frontend/` (`pnpm lint`; config `eslint.config.mjs`, Next's core-web-vitals and
TypeScript rules), and any error or warning fails CI. There is no Prettier setup. The other static
check is the TypeScript compiler (`pnpm typecheck`); SonarCloud analyses the code in CI (see the
[CI and merge gates reference](../reference/ci-and-merge-gates.md)).

## Add a page route

1. Create `frontend/app/<route>/page.tsx` with a default export that renders `<AppBar />`, a back
    link, and one client component. Copy the shape of `frontend/app/admin/page.tsx`:

    ```tsx
    import { AppBar } from "@/components/app/app-bar";
    import { BackLink } from "@/components/app/back-link";
    import { ReportsView } from "@/components/reports/reports-view";

    /** Reports. Unauthenticated requests 401 -> /login. */
    export default function ReportsPage() {
      return (
        <>
          <AppBar />
          <div className="ev-page-back">
            <BackLink />
          </div>
          <ReportsView />
        </>
      );
    }
    ```

    For a dynamic segment, `params` is a Promise; await it as `frontend/app/records/[id]/page.tsx`
    does.
2. Put the view in `frontend/components/<area>/`, marked `"use client"`. All data fetching happens
    there, through hooks; pages do no server-side fetching.
3. If the view reads `useSearchParams`, wrap it in `<Suspense>` in the page, as
    `frontend/app/login/page.tsx` does. `next build` fails without it.
4. Do not choose a path under `/api/` or `/docs/`. The nginx proxy sends those to the API and the
    documentation site, so Next never sees them (`deploy/nginx.conf`).
5. Link it from the user menu: add a `DropdownMenuItem` in `frontend/components/app/user-menu.tsx`
    `UserMenu()`. Gate an admin-only item on `user?.is_superuser`, as the Admin item is.
6. The frontend does not protect routes. The backend answers 401 or 403 and the client reacts (a 401
    goes to `/login`). For an admin-only page, also show a notice to a non-admin, as `AdminView` does.
7. Add a case to `frontend/app/route-pages.test.tsx`: stub the child components and assert the page
    renders the app bar and passes the right props.
8. Add the route to the Routes table in
    [the frontend routes and data reference](../reference/frontend-routes-and-data.md), with the path
    written literally.

## Add a backend call and a hook

1. Add the endpoint on the backend first; see
    [how to add an API route or export](add-an-api-route-or-export.md).
2. Add a typed function to the matching file in `frontend/lib/` (`documents-api.ts`, `review-api.ts`,
    `admin-api.ts`, `auth-api.ts`) that calls `apiFetch<T>(path, options)` from `frontend/lib/api.ts`.
    Pass the path without `/api`. Send JSON with `body: JSON.stringify(...)`; `apiFetch()` sets the
    content type. Send files as `FormData`.
3. If the endpoint produces a file, do not fetch it. Call `downloadFile(path, body, fallbackName)` from
    `frontend/lib/download.ts`, keep the returned `PreparedDownload` in state, and pass it to
    `useDownloadWatch()` to show how the download ended. `ExportDialog` and `BundlePageClient` show the
    pattern.
4. Add a hook in `frontend/hooks/`:
    - Reads: `useQuery({ queryKey, queryFn })` with a stable key. Export a key function (as
      `summariesKey()` and `duplicatesKey()` do) when more than one place uses it.
    - Polling: `refetchInterval: (query) => (condition ? 2000 : false)`, so it stops when the work
      stops (`useDocuments()`, `useDuplicates()`).
    - Writes: `useMutation()` that invalidates every dependent key in `onSuccess`
      (`frontend/hooks/use-admin.ts` `usePromptMutation()`), or patches the cache with the item the
      server returns (`frontend/hooks/use-summaries.ts`).
5. Show failures with `humanizeError(err, { fallback, notFound })` from `frontend/lib/errors.ts`, never
    `err.message`. A 401 is already handled for every query and mutation.
6. If the call writes review rows on the server and the workbench can be open, call the workbench's
    `reloadRows()` after it succeeds, or the editor's next autosave sends the old values back.
7. Tests:
    - `frontend/lib/<file>.test.ts`: stub `fetch` with `vi.stubGlobal("fetch", vi.fn())` and assert the
      URL, method and body (pattern: `frontend/lib/admin-api.test.ts`).
    - `frontend/hooks/<hook>.test.tsx`: `vi.mock("@/lib/<file>")`, render the hook inside a fresh
      `QueryClient` with `retry: false`, and assert what it invalidates (pattern:
      `frontend/hooks/use-admin.test.tsx`).
8. Add the call, its key and any polling interval to the reference tables.

## Add a category bundle

1. Add a `BundleConfig` to `frontend/lib/bundle-api.ts` and append it to `BUNDLES`. Give it `label`,
    a URL-safe `slug`, `categories` (category ids as strings), `downloadName`, and `coverHeading` only
    if the combined PDF should start with a list page. Set `separateAs` (for example `"Deposition"`)
    instead when each document should download as its own dated PDF, as Depositions does; the page
    button then reads "Download separate PDFs". Set `summarizedOnly: true` when the bundle should carry
    only the rows ticked for summary, as Diagnostic & Operative does. The backend keeps no copy of this list; the
    bundle and zip endpoints receive these values.
2. Create a route page that passes the config, copying `frontend/app/diagnostics/page.tsx`.
3. Add a tab to `BUNDLE_TABS` in `frontend/components/bundle/bundle-page-client.tsx`, with `value`
    equal to the slug and `href` equal to the route.
4. Add a menu item for it in `UserMenu()`.
5. Nothing else is needed for the zip export: `ExportDialog` sends every entry of `BUNDLES`.
6. Tests: add a case to `frontend/app/route-pages.test.tsx` that checks the page passes this exact
    config object (by identity), and extend `frontend/lib/bundle-api.test.ts`.

## Add a report-header field

1. Backend first: the column and its migration
    ([how to create a database migration](create-a-database-migration.md)), the field in `HeaderPayload`
    (`backend/app/schemas/documents.py`), the assignment in `put_header()`
    (`backend/app/api/documents.py`), and the value in `Document.listing()` (`backend/app/models.py`).
    Every `HeaderPayload` field defaults to empty, so a save that leaves a field out clears it.
2. Add the field to `HeaderFields` in `frontend/lib/review-api.ts`, and as an optional field to
    `DocumentListItem` and `DocumentDetail` in `frontend/lib/types.ts`.
3. Add it to `EMPTY` and to the form in `frontend/components/review/header-bar.tsx`. The Save button
    sends the whole `fields` object, so the new field is sent once it is in the state.
4. Seed it in both `setHeader({ ... })` calls in `frontend/hooks/use-review-workflow.ts`: in the boot
    effect and in `watchSegment()`.
5. If an export needs it, add it to the body in `frontend/components/review/export-dialog.tsx` or
    `frontend/lib/bundle-api.ts`, and accept it on the backend.
6. Tests: `frontend/components/review/header-bar.test.tsx` (the field renders, saves, and is present
    after Auto-fill) and `frontend/hooks/use-review-workflow.test.tsx` (the field is seeded from the
    record).

## Add a row field

1. Backend first: the column and its migration, `ReviewRow.as_row()` (`backend/app/models.py`),
    `_editor_row()` and `_store_rows()` (`backend/app/api/documents.py`), and `RowsPayload`
    (`backend/app/schemas/documents.py`).
2. Add the field to `Row` in `frontend/lib/types.ts`. Make it optional when the server computes it
    and the editor never sets it (as `method` and `ruled_paperwork` are).
3. Render and edit it in `RowsTable` (`frontend/components/review/rows-table.tsx`) with
    `onField(i, { <field>: value })`, so every edit passes through `ReviewEditor`'s `field()`.
4. Give it a value in the rows the editor creates, `splitConfirm()` and `confirmAdd()` in
    `frontend/components/review/review-editor.tsx`, and decide how `mergeRows()` in
    `frontend/lib/review-rows.ts` combines it. By default a merge keeps the upper row's value.
5. If editing it means the reviewer has dealt with the row, add it to `ADJUDICATING_FIELDS` in
    `frontend/lib/review-rows.ts` so the review flag clears.
6. If another workbench tab can change it on the server, add it to `SERVER_WRITABLE_FIELDS` and also
    copy it in `applyServerRowChanges()`, which names `include` and `category` explicitly. Doing only
    the first leaves the field reverted by the next autosave.
7. If it has a validity rule, change `rowErrors()` in `frontend/lib/review-rows.ts` and
    `validate_rows()` in `backend/app/services/rows.py` together.
8. Tests: `frontend/lib/review-rows.test.ts` (and `review-rows.property.test.ts` for an invariant that
    must hold for any rows), `frontend/components/review/rows-table.test.tsx`,
    `frontend/components/review/review-editor.test.tsx`, and
    `frontend/hooks/use-review-workflow.touched-keys.test.tsx` when the field is server-writable.

## Add a summary-card flag

1. Backend first: add the field to the summary payload (`Summary.listing()` in `backend/app/models.py`
    and `_summary_response()` in `backend/app/api/documents.py`).
2. Add it to `SummaryItem` in `frontend/lib/types.ts` as an optional field. Absent must mean "nothing
    to say", so an older backend during a rolling deploy flags nothing.
3. Add a chip to `SummaryChips` in `frontend/components/review/summaries-view.tsx`, in the documented
    order: what the reviewer did, then what the system flagged, then what is stale, then `Excluded`.
    Use `ev-chip ev-chip-review` and a `title` that says what to do next.
4. Tests: `frontend/components/review/summaries-view.test.tsx`, including a case where the field is
    absent.

## Add a job stage label, job state or document status

1. Backend first; see [how to add a job kind or stage](add-a-job-kind-or-stage.md).
2. Stage label: add it to `STAGE_LABELS` in `frontend/hooks/use-review-workflow.ts`. An unknown stage
    shows its raw name.
3. Job state: add it to `JobState` in `frontend/lib/types.ts` and name it in `pollJob()`. A state
    `pollJob()` does not name keeps the page polling forever. If the Duplicates tab should poll during
    it, extend the condition in `useDuplicates()`.
4. Document status: add it to `DocumentStatus` in `frontend/lib/types.ts`, to `STATUS_LABELS` and
    `STATUS_TONES` in `frontend/components/documents/status-pill.tsx`, to `FILTERS` in
    `frontend/components/documents/documents-table.tsx` if it needs its own chip, and to the boot
    decisions in `useReviewWorkflow()`.
5. Tests: `frontend/hooks/use-review-workflow.test.tsx`, `frontend/components/documents/status-pill.test.tsx`
    and `frontend/components/documents/documents-table.test.tsx`.

## Style a component

1. Use the design-system classes and tokens in `frontend/app/evaluators-ds.css`, or Tailwind utilities
    with the bridged names (`bg-navy-600`, `text-danger`). Both are listed in the
    [design system reference](../reference/design-system.md). Use `var(--gutter)` for a page column's
    side padding.
2. Put a new design-system class in the matching section of `evaluators-ds.css`, with the family's
    prefix.
3. For a new Radix primitive, add it with the shadcn CLI, which writes to `frontend/components/ui/`
    using `frontend/components.json`:

    ```bash
    cd frontend
    pnpm exec shadcn add popover
    ```

    Its colours come from the semantic slots in `frontend/app/globals.css`; do not hard-code hex values.
4. Check the layout below each width the CSS uses (720, 900 and 1024 px).

## Tests to add, and the rules they run under

Vitest collects `{app,lib,hooks,components}/**/*.{test,spec}.{ts,tsx}` and runs every file in one
shared jsdom worker (`frontend/vitest.config.mts`: `pool: "threads"`, `isolate: false`). The shared
worker is guarded for modules (reset per file in `frontend/vitest.setup.ts`), `vi.stubGlobal` (undone
before each test), the DOM (unmounted after each test) and storage (cleared after each test). You must
restore these yourself, in the same file:

- anything assigned directly on `window` or `document`;
- an environment variable set with `vi.stubEnv` or written to `process.env`;
- a redefinition on a prototype or a built-in (`Object.defineProperty`);
- module state inside a package in `node_modules`, such as a Testing Library `configure()` call;
- fake timers and a mocked date: a file that ends with either still on fails, so call
  `vi.useRealTimers()` in its own `afterEach` or `afterAll`.

A test in a new top-level folder is not collected until the folder is added to `include` in
`frontend/vitest.config.mts`; add it to the coverage `include` too. Property tests use
`@fast-check/vitest` (`frontend/lib/review-rows.property.test.ts`).

Playwright specs live in `frontend/e2e/*.spec.ts` and run against a live stack, never a mocked one:

- Start each spec with `registerAndLogin(page)` from `frontend/e2e/support.ts`, which registers a fresh
  account on the reserved `example.com` domain, so specs never share state.
- Use only synthetic fixtures in `frontend/e2e/fixtures/`. They are committed through an exception in
  `frontend/.gitignore`, because PDFs are ignored everywhere else.
- CI starts the API, the web app and the proxy but no AI workers, so identification and summarization
  cannot be tested end to end.

## Verify it worked

From `frontend/`:

```bash
pnpm lint
```

```bash
pnpm typecheck
```

```bash
pnpm test
```

```bash
pnpm build
```

```bash
pnpm test:coverage
```

CI fails the frontend below 80% on any of statements, branches, functions or lines; `pnpm test:coverage`
prints the same four numbers. To run part of the suite, give vitest a path fragment:

```bash
pnpm exec vitest run components/review
```

End-to-end, with the app running on port 8080 (install the browser once):

```bash
pnpm exec playwright install chromium
```

```bash
pnpm e2e
```

To point Playwright at another origin, set `E2E_BASE_URL`:

```bash
E2E_BASE_URL=http://localhost:8080 pnpm e2e
```

Then open the changed page in the running app. The app image is baked, so rebuild the `web` service
before expecting the container to show a frontend change; see
[how to run the app locally](run-the-app-locally.md).

## If it fails

- A test passes alone and fails in the full run: something leaked across files in the shared worker.
  Check the list above; `frontend/vitest.setup.ts` explains each guard.
- A new test file is never run: its folder is not in the vitest `include` list.
- `localStorage` is undefined in tests on a newer Node: `frontend/vitest.setup.ts` installs an
  in-memory store only when the global is empty. Keep that shim; CI runs Node 24.
- `next build` complains about `useSearchParams`: wrap the component in `<Suspense>` in its page.
- An edit on Review & correct reverts after an action on another tab: the action did not call
  `reloadRows()`, or a new server-writable field is missing from `applyServerRowChanges()`.
- To undo a change, revert the commit; nothing here changes stored data unless a backend migration
  came with it.

## Related pages

- [The record workbench](../explanation/frontend-workbench.md)
- [Frontend routes and data reference](../reference/frontend-routes-and-data.md)
- [Design system reference](../reference/design-system.md)
- [How to run the tests](run-the-tests.md)
- [How to add an API route or export](add-an-api-route-or-export.md)
- [HTTP API reference](../reference/http-api.md)

<!-- reviewed: 2026-09-30 -->
