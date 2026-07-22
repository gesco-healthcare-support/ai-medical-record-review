import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { humanizeError } from "@/lib/errors";

// Expected copy is derived from the contract (status -> guidance), NOT read off the implementation:
// 404 must become guidance (the server keeps it IDOR-vague), network + bodiless-500 get safe copy,
// and genuinely actionable server details (400/409/503-pipeline) are preserved verbatim.
describe("humanizeError", () => {
  it("maps a network failure (ApiError status 0) to a connection message", () => {
    expect(humanizeError(new ApiError("network", 0))).toMatch(/couldn't reach the server/i);
  });

  it("maps a non-ApiError to the generic message", () => {
    expect(humanizeError(new Error("boom"))).toMatch(/something went wrong/i);
  });

  it("maps 401 to a session message and 403 to a permission message", () => {
    expect(humanizeError(new ApiError("signed out", 401))).toMatch(/session has ended/i);
    expect(humanizeError(new ApiError("forbidden", 403))).toMatch(/permission/i);
  });

  it("turns the IDOR-vague 404 into guidance, never 'not found'", () => {
    const msg = humanizeError(new ApiError("not found", 404));
    expect(msg).not.toBe("not found");
    expect(msg).toMatch(/no longer available/i);
  });

  it("uses a caller-supplied 404 message when given", () => {
    const msg = humanizeError(new ApiError("not found", 404), {
      notFound: "This record is no longer available to you.",
    });
    expect(msg).toBe("This record is no longer available to you.");
  });

  it("replaces apiFetch's synthetic '<path> failed (500)' with a generic message", () => {
    expect(humanizeError(new ApiError("/documents/x failed (500)", 500))).toMatch(
      /something went wrong/i,
    );
  });

  it("preserves an actionable 400/409 server detail", () => {
    expect(humanizeError(new ApiError("row 3: overlaps or is out of order with the previous row", 400))).toBe(
      "row 3: overlaps or is out of order with the previous row",
    );
    expect(humanizeError(new ApiError("a job is already running for this document", 409))).toBe(
      "a job is already running for this document",
    );
  });

  it("preserves the friendly pipeline message on a 503 (does not clobber with generic)", () => {
    const ocr = "Text recognition (OCR) is unavailable on the server, so this document could not be read.";
    expect(humanizeError(new ApiError(ocr, 503))).toBe(ocr);
  });
});
