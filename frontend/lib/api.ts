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
  if (resp.status === 401) {
    // Session gone -> go to login (guard against a loop when already there).
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
    throw new ApiError("signed out", 401);
  }
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    const body = data as { detail?: string; error?: string } | null;
    const message = body?.detail ?? body?.error ?? `${path} failed (${resp.status})`;
    throw new ApiError(message, resp.status);
  }
  return data as T;
}
