# frontend/lib - agent instructions

## API client rules

- Every JSON call goes through `apiFetch()`. Pass the path WITHOUT `/api`. Do not add a CSRF header: the
  backend uses a SameSite=Lax session cookie with no CSRF token.
- Every file goes through `downloadFile()`. Never read a file into a Blob and never write a bespoke
  `fetch` for a download: the Blob path was cut short by Chrome on a low-disk machine (#389), and three
  hand-written copies had drifted in how they failed.
- Both clients fail with `ApiError(message, status)`; status 0 is a transport failure. `humanizeError()`
  keeps a server sentence only from an `ApiError`, so never throw a plain `Error` carrying a server
  message.
- A 401 always goes through `signedOut()` and is THROWN, never returned: a returned value lets a caller
  report a signed-out request as a success.
- `errorFromResponse()` takes `detail` only when it is a string. FastAPI's 422 puts a list there, which
  would render as "[object Object]".
- The download failure log (`reportFailure()`) records phase, status and expected bytes only. Never log
  the body or the file name; the file name carries the patient's name.

## Wire types (`types.ts`)

- Mirror the backend payloads. A field the backend adds later is OPTIONAL here, and absent must mean
  "nothing to say" (rolling deploys serve old and new backends side by side).
- Values are strings with sentinels: `flag` is `"x"` or `"-"`, an empty title or date is `"-"`,
  category ids are strings (`"100"` is General). Compare categories with `String(a) === String(b)`.
- `Row.method` absent or null means UNKNOWN. `couldNotIdentify()` treats unknown as not confident;
  `categoryWasGuessed()` treats it as not guessed. The asymmetry is deliberate.

## Row rules (`review-rows.ts`)

- `keySeq` is module-global and never reset, per document or otherwise. Keys are also touched-set
  identities.
- `mergeRows()` keeps the UPPER row's identity; `include` is true if either half was included; `flag`
  is `x` if either half was flagged.
- `applyServerRowChanges()` matches rows by `start-end` span and copies only `include` and `category`,
  named explicitly. A new server-writable field goes in `SERVER_WRITABLE_FIELDS` AND in that function.
- `rowErrors()` mirrors the page-range half of `backend/app/services/rows.py` `validate_rows()`: whole
  numbers, `1 <= start <= end <= totalPages`, no overlap with the previous row, gaps allowed. Change
  both sides together. The server also rejects an empty set and an inactive category id.
- `clearFlagOnEdit()` clears the flag only on a REAL change to `ADJUDICATING_FIELDS`; never on `flag`
  or `include`.
- `moveSharedBoundary()` moves the previous row's end only when the two rows were contiguous BEFORE the
  edit.
- `categoryWasGuessed()` must stay out of the "Could not identify" filter: `method` is frozen at segment
  time, so the reviewer could never clear it.

## Bundles (`bundle-api.ts`)

- `BUNDLES` is the only copy of the bundle taxonomy; the backend receives it per request. The export
  zip sends every entry, so adding a bundle here changes the zip.

## Tests

- Stub `fetch` with `vi.stubGlobal("fetch", vi.fn())`; it is undone before each test by
  `unstubGlobals`. Anything assigned on `window` or `document` directly must be restored in the file.
- Add a property test (`review-rows.property.test.ts`) for any new invariant over arbitrary rows.

## Commands

Run from `frontend/`:

```bash
pnpm exec vitest run lib
pnpm typecheck
```

## Docs

- `docs/reference/frontend-routes-and-data.md` (calls, types, error mapping)
- `docs/explanation/frontend-workbench.md` (row rules, downloads)
