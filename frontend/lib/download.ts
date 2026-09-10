/**
 * The one place a streamed file download becomes a saved file or a sentence.
 *
 * Downloads cannot go through `apiFetch`: they read a blob and a `Content-Disposition` filename
 * rather than JSON. So each one grew its own `fetch`, and the three copies drifted in exactly the
 * ways that matter to the reviewer:
 *
 *   - the bundle download read the server's `detail` and threw it as a plain `Error`, which
 *     `humanizeError` discards by design (it only preserves a message from an `ApiError`), so the
 *     reason was extracted and then dropped one frame later. `bundle-api.test.ts` pins the throw
 *     and passes; nothing pinned what the screen shows.
 *   - the export dialog did not read the body at all, throwing a bare `export failed (500)`.
 *   - both RETURNED on a 401 after redirecting, so the caller saw a resolved promise and reported
 *     the download as finished while the browser was navigating to /login.
 *
 * One function, so the next download cannot invent a fourth behaviour, and it fails the same way
 * `apiFetch` does: `ApiError` with a status, which is the only shape `humanizeError` can turn into
 * the server's own words.
 */

import { ApiError, errorFromResponse, signedOut } from "@/lib/api";

/** Filename from `Content-Disposition`, or the caller's fallback. */
function filenameFrom(disposition: string | null, fallbackName: string): string {
  const match = /filename="?([^"]+)"?/.exec(disposition ?? "");
  return match ? match[1] : fallbackName;
}

/** Hand the blob to the browser as a download. Detached anchor: it needs no document position. */
function save(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * POST `body` to `/api${path}` and save the file that comes back.
 *
 * Throws `ApiError` on every failure - status 0 for a transport failure, 401 after redirecting,
 * the server's own message otherwise - so a caller can hand the error straight to `humanizeError`.
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
    throw new ApiError("network", 0);
  }
  if (resp.status === 401) throw signedOut();
  if (!resp.ok) throw await errorFromResponse(resp, path);
  save(await resp.blob(), filenameFrom(resp.headers.get("Content-Disposition"), fallbackName));
}
