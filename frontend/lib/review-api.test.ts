/**
 * The job-control client calls. These are thin wrappers, but the URL and the body ARE the contract:
 * `cancelJob` addresses one job by id rather than "whatever is active on this document", and it is
 * the `force` flag alone that separates asking a run to stop from killing its work-horse. A wrong
 * path or a dropped flag turns the reviewer's first press into a hard kill, or a stop into a no-op,
 * and neither shows up as a type error.
 *
 * Stubbed at `fetch` rather than at `apiFetch`, so the assertions cover the request the browser would
 * really send - including the `/api` prefix and the JSON content type that apiFetch adds.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cancelJob, startDedup, startSegment } from "@/lib/review-api";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(ok({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall() {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url, init, body: init?.body ? JSON.parse(init.body as string) : undefined };
}

describe("cancelJob", () => {
  it("addresses the job by id and defaults to a cooperative stop", async () => {
    fetchMock.mockResolvedValue(ok({ id: 42, state: "running", graceSeconds: 10 }));

    const result = await cancelJob("doc-1", 42);

    const { url, init, body } = lastCall();
    // The job id in the path is what stops a cancel from hitting a job that started since the render.
    expect(url).toBe("/api/documents/doc-1/jobs/42/cancel");
    expect(init?.method).toBe("POST");
    expect(body).toEqual({ force: false });
    // graceSeconds is passed through untouched: the button's escalation moment comes from the server.
    expect(result.graceSeconds).toBe(10);
  });

  it("sends force only when the caller asks for it", async () => {
    fetchMock.mockResolvedValue(ok({ id: 42, graceSeconds: 10 }));
    await cancelJob("doc-1", 42, true);
    expect(lastCall().body).toEqual({ force: true });
  });

  it("carries the credentials the session cookie needs", async () => {
    fetchMock.mockResolvedValue(ok({ id: 1, graceSeconds: 5 }));
    await cancelJob("doc-1", 1);
    expect(lastCall().init?.credentials).toBe("include");
  });
});

describe("restart starts", () => {
  it("startSegment defaults to continuing, and sends fresh for Start over", async () => {
    await startSegment("doc-1");
    expect(lastCall().url).toBe("/api/documents/doc-1/segment/start");
    expect(lastCall().body).toEqual({ fresh: false });

    await startSegment("doc-1", true);
    expect(lastCall().body).toEqual({ fresh: true });
  });

  it("startDedup defaults to continuing, and sends fresh for Start over", async () => {
    await startDedup("doc-1");
    expect(lastCall().url).toBe("/api/documents/doc-1/dedup/start");
    expect(lastCall().body).toEqual({ fresh: false });

    await startDedup("doc-1", true);
    expect(lastCall().body).toEqual({ fresh: true });
  });
});
