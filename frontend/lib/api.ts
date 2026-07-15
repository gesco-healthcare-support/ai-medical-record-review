// Same-origin API client for the FastAPI backend (proxied via next.config rewrites in dev,
// a reverse proxy in prod). Sends the session cookie automatically (credentials: "include")
// and echoes the CSRF cookie as a header on unsafe methods - the pattern the backend expects
// (finalized in P2 auth). 401 -> the caller redirects to the login route.

const XSRF_COOKIE = "XSRF-TOKEN";
const XSRF_HEADER = "X-XSRF-Token";

function readCookie(name: string): string {
  if (typeof document === "undefined") return ""; // no cookies during SSR
  const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (method !== "GET" && method !== "HEAD") {
    headers.set(XSRF_HEADER, readCookie(XSRF_COOKIE));
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  }
  const resp = await fetch(`/api${path}`, { ...options, headers, credentials: "include" });
  if (resp.status === 401) throw new ApiError("signed out", 401);
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    const message = (data as { error?: string } | null)?.error ?? `${path} failed (${resp.status})`;
    throw new ApiError(message, resp.status);
  }
  return data as T;
}
