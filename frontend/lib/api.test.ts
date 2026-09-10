import { describe, expect, it } from "vitest";

import { ApiError, errorFromResponse } from "@/lib/api";
import { humanizeError } from "@/lib/errors";

/** `errorFromResponse` is the one reading of "what did this failed response say", shared by the
 *  JSON client and the download client. These cover the shapes a real server sends. */
function response(status: number, body: unknown, parseable = true): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => {
      if (!parseable) throw new SyntaxError("not json");
      return body;
    },
  } as unknown as Response;
}

describe("errorFromResponse", () => {
  it("carries the server's message and status, so humanizeError can preserve it", async () => {
    const err = await errorFromResponse(response(422, { detail: "One document is blank." }), "/x");
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(humanizeError(err, { fallback: "Export failed." })).toBe("One document is blank.");
  });

  it("refuses a NON-STRING detail instead of stringifying it", async () => {
    // FastAPI's own validation errors put a list of {loc, msg, type} objects in `detail` - verified
    // against the live app on PUT /documents/{id}/rows and POST /documents/{id}/export. Taken
    // as-is it reaches `new ApiError(...)` as an array, `Error` stringifies it, and the reviewer is
    // shown the literal text "[object Object]" - because humanizeError preserves a 422 message,
    // which is right for every other 422 the app sends.
    const detail = [{ type: "list_type", loc: ["body", "rows"], msg: "Input should be a list" }];
    const err = await errorFromResponse(response(422, { detail }), "/documents/d1/rows");

    expect(err.message).not.toContain("[object Object]");
    expect(humanizeError(err, { fallback: "Could not save." })).toBe("Could not save.");
  });

  it("falls back when the body is not JSON at all", async () => {
    const err = await errorFromResponse(response(502, null, false), "/x");
    expect(err.status).toBe(502);
    // The synthesized "…failed (502)" shape is what humanizeError recognises and replaces.
    expect(humanizeError(err)).toMatch(/something went wrong/i);
  });

  it("reads `error` when the server used that key instead", async () => {
    const err = await errorFromResponse(response(409, { error: "A job is running." }), "/x");
    expect(humanizeError(err, { fallback: "no" })).toBe("A job is running.");
  });
});
