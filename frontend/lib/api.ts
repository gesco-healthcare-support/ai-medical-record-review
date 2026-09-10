// Same-origin API client for the FastAPI backend (proxied via next.config rewrites in dev,
// a reverse proxy in prod). Sends the session cookie automatically (credentials: "include").
// The backend uses a SameSite=Lax session cookie with no CSRF token (P2 auth), so no
// double-submit header is needed. A 401 means the session is gone -> callers redirect to /login.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Session gone: send the reviewer to /login (guarding a loop when already there) and hand back the
 *  error to throw. Every client throws it, so none of them can report a signed-out request as a
 *  success - the streamed-download path used to `return` here and its caller then said the file had
 *  downloaded. */
export function signedOut(): ApiError {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
  return new ApiError("signed out", 401);
}

/**
 * The single reading of "what did this failed response say". Shared with the download path so the
 * two clients cannot disagree about it, and so a download reaches `humanizeError` as an `ApiError`
 * with a status - which is the only form that function preserves a server message from.
 *
 * `detail` is taken ONLY when it is a string. FastAPI's own validation errors put a LIST of
 * `{loc, msg, type}` objects there - verified against the live app on `PUT /documents/{id}/rows`
 * and `POST /documents/{id}/export` - and an array reaching `new ApiError(...)` stringifies to
 * "[object Object]", which `humanizeError` then shows verbatim because a 422 carries an actionable
 * message on every other path. A non-string detail is not a sentence, so it falls through to the
 * synthesized fallback, which `humanizeError` recognises and replaces with safe copy.
 */
export async function errorFromResponse(resp: Response, path: string): Promise<ApiError> {
  const data = await resp.json().catch(() => null);
  const body = data as { detail?: unknown; error?: unknown } | null;
  const detail = typeof body?.detail === "string" ? body.detail : undefined;
  const error = typeof body?.error === "string" ? body.error : undefined;
  return new ApiError(detail ?? error ?? `${path} failed (${resp.status})`, resp.status);
}

export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  // FormData sets its own multipart boundary content-type; only default to JSON otherwise.
  if (
    method !== "GET" &&
    method !== "HEAD" &&
    options.body &&
    !headers.has("Content-Type") &&
    !(options.body instanceof FormData)
  ) {
    headers.set("Content-Type", "application/json");
  }
  let resp: Response;
  try {
    resp = await fetch(`/api${path}`, { ...options, headers, credentials: "include" });
  } catch {
    // Network / transport failure (offline, DNS, connection reset) - not an HTTP status. Surface
    // as ApiError(status 0) so callers + humanizeError treat it as "couldn't reach the server".
    throw new ApiError("network", 0);
  }
  if (resp.status === 401) throw signedOut();
  if (!resp.ok) throw await errorFromResponse(resp, path);
  return (resp.status === 204 ? null : await resp.json().catch(() => null)) as T;
}
