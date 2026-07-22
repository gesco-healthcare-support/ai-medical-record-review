import { ApiError } from "@/lib/api";

/** Optional per-call-site copy: a tailored 404 message and/or a tailored generic-failure fallback. */
export type ErrorContext = { notFound?: string; fallback?: string };

const NETWORK = "Couldn't reach the server. Check your connection and try again.";
const GENERIC =
  "Something went wrong on our end. Please try again; if it keeps failing, contact your administrator.";
const SESSION = "Your session has ended. Please sign in again.";
const FORBIDDEN = "You don't have permission to do that.";
const NOT_FOUND =
  "This item is no longer available - it may have been deleted or moved. Refresh and try again.";

// apiFetch's synthetic message when the server sent no detail/error body, e.g. "/documents/x failed (500)".
const SYNTHETIC_FALLBACK = /failed \(\d+\)$/;

/**
 * Turn any thrown error into a clear, user-facing sentence. Keyed on HTTP status so the terse,
 * IDOR-vague server 404 ("not found") becomes guidance, while genuinely actionable server details
 * (400/409/422 and the friendly AI/pipeline messages the server already sends on 500/503) are
 * preserved. A network drop (ApiError status 0) and a bodiless server error both get safe copy.
 */
export function humanizeError(err: unknown, ctx: ErrorContext = {}): string {
  if (!(err instanceof ApiError)) return ctx.fallback ?? GENERIC;
  const { status, message } = err;
  if (status === 0) return NETWORK;
  if (status === 401) return SESSION;
  if (status === 403) return FORBIDDEN;
  if (status === 404) return ctx.notFound ?? NOT_FOUND;
  // No server-provided message (apiFetch synthesized the fallback) -> generic; never show "failed (500)".
  if (SYNTHETIC_FALLBACK.test(message)) return ctx.fallback ?? GENERIC;
  // 400 / 409 / 422 / 503 / 500-with-body: the server sent an actionable, human message - use it.
  return message;
}
