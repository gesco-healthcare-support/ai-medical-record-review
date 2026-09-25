/**
 * The one place an export becomes a downloaded file or a sentence.
 *
 * Downloads cannot go through `apiFetch`, and each one used to grow its own `fetch`; the three copies drifted
 * in exactly the ways that matter to the reviewer:
 *
 *   - the bundle download read the server's `detail` and threw it as a plain `Error`, which
 *     `humanizeError` discards by design (it only preserves a message from an `ApiError`), so the
 *     reason was extracted and then dropped one frame later.
 *   - the export dialog did not read the body at all, throwing a bare `export failed (500)`.
 *   - both RETURNED on a 401 after redirecting, so the caller saw a resolved promise and reported
 *     the download as finished while the browser was navigating to /login.
 *
 * One function, so the next download cannot invent a fourth behaviour, and it fails the same way `apiFetch`
 * does: `ApiError` with a status, which is the only shape `humanizeError` can turn into the server's words.
 *
 * NO BLOB (#389). This used to read the whole file with `resp.blob()` and save it through an object URL.
 * Chrome keeps a large Blob in memory only up to an allowance, then pages it to disk - and on a machine short
 * of disk space it cancels the body part-way instead: on the shared reviewer host three of four exports were
 * cut short on 2026-09-24 (15.8 of 19.5 MB, 16.8 of 22.2, 16.2 of 37.0). So the POST now builds the file and
 * answers with WHERE to fetch it, and an ordinary link hands that address to the browser's own download
 * manager, which streams it straight to disk.
 */

import { ApiError, errorFromResponse, signedOut } from "@/lib/api";
import { lacksServerMessage } from "@/lib/errors";

/** What an export POST answers with: the prepared file's address, and the name to save it under. */
type PreparedDownload = { url: string; filename?: string };

/** #390: the build answer's headers arrived but its body could not be read. */
export const DOWNLOAD_INTERRUPTED = "The download was interrupted before it finished. Please try again.";
/** #390: the proxy itself answered 502/504 (the API restarting, or a build past nginx's time limit), so
 *  there is no sentence of the server's own to show. */
export const DOWNLOAD_NOT_PREPARED = "The server did not finish preparing the file. Please try again.";

/** The detail a reviewer's own browser can hold when a download fails (#390): which phase, the HTTP status
 *  (0 for no response at all), and the size the server declared. Never the body and never the filename -
 *  the filename carries the patient's name. */
function reportFailure(status: number, resp?: Response): void {
  const length = resp?.headers.get("Content-Length");
  console.error("download failed", {
    phase: "prepare",
    status,
    expectedBytes: length ? Number(length) : null,
  });
}

/**
 * POST `body` to `/api${path}`, then hand the file it prepared to the browser as a native download.
 *
 * Throws `ApiError` on every failure - status 0 for a transport failure, 401 after redirecting, the server's
 * own message otherwise - so a caller can hand the error straight to `humanizeError`. Resolves once the
 * download has been handed to the browser; the transfer itself belongs to the browser from there.
 */
export async function downloadFile(
  path: string,
  body: unknown,
  fallbackName: string,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
  } catch {
    // Transport failure (offline, DNS, reset) - no HTTP status. Same shape apiFetch uses.
    reportFailure(0);
    throw new ApiError("network", 0);
  }
  if (resp.status === 401) {
    reportFailure(401, resp);
    throw signedOut();
  }
  if (!resp.ok) {
    reportFailure(resp.status, resp);
    const err = await errorFromResponse(resp, path);
    if ((resp.status === 502 || resp.status === 504) && lacksServerMessage(err)) {
      throw new ApiError(DOWNLOAD_NOT_PREPARED, resp.status);
    }
    throw err;
  }
  let prepared: PreparedDownload;
  try {
    prepared = (await resp.json()) as PreparedDownload;
  } catch {
    // Headers arrived, the body did not: without this the dialog's generic fallback hid the cause (#390).
    reportFailure(resp.status, resp);
    throw new ApiError(DOWNLOAD_INTERRUPTED, resp.status);
  }
  // A detached anchor: it needs no place in the document. Same-origin, so the session cookie goes with it.
  const link = document.createElement("a");
  link.href = prepared.url;
  link.download = prepared.filename || fallbackName;
  link.click();
}
