/**
 * The documents client calls.
 *
 * Two of these send `FormData`, and the header behaviour around that is the whole reason this file
 * stubs `fetch` rather than `apiFetch`: the guarantee worth having is about a header that `apiFetch`
 * deliberately does NOT set, and stubbing one layer higher would hide it entirely.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  aggregateDocuments,
  deleteDocument,
  listDocuments,
  startIdentification,
  uploadDocument,
} from "@/lib/documents-api";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

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
  return { url: url as string, init: init as RequestInit };
}

const pdf = (name = "record.pdf") => new File(["%PDF-"], name, { type: "application/pdf" });

describe("uploading", () => {
  it("sends the file as multipart and lets the browser set the boundary", async () => {
    const file = pdf();
    await uploadDocument(file);

    const { url, init } = lastCall();
    expect(url).toBe("/api/documents");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("pdf")).toBe(file);

    // Load-bearing ABSENCE. A multipart Content-Type is only valid with the boundary the browser
    // generates; a hand-set `multipart/form-data` omits it and the server cannot parse the body.
    // apiFetch skips its JSON default for FormData precisely so this header stays unset.
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("omits the record name entirely when it is blank, and trims it when it is not", async () => {
    // "" and "absent" are different requests: the server names the record from the files when no
    // name is sent, and an empty string would override that with nothing.
    await aggregateDocuments("   ", [pdf("a.pdf"), pdf("b.pdf")]);
    expect((lastCall().init.body as FormData).has("name")).toBe(false);

    await aggregateDocuments("  Combined record  ", [pdf("a.pdf")]);
    expect((lastCall().init.body as FormData).get("name")).toBe("Combined record");
  });

  it("sends every file of an aggregate under the same field", async () => {
    const files = [pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")];
    await aggregateDocuments("Combined record", files);

    const { url, init } = lastCall();
    expect(url).toBe("/api/documents/aggregate");
    // getAll, not get: a loop that overwrote instead of appending would upload only the last file
    // and lose the rest without any error.
    expect((init.body as FormData).getAll("pdfs")).toEqual(files);
  });
});

describe("the plain document calls", () => {
  it("each address their own path and method", async () => {
    await listDocuments();
    expect(lastCall().url).toBe("/api/documents");
    expect(lastCall().init.method ?? "GET").toBe("GET");

    await deleteDocument("d1");
    expect(lastCall().url).toBe("/api/documents/d1");
    expect(lastCall().init.method).toBe("DELETE");

    await startIdentification("d1");
    expect(lastCall().url).toBe("/api/documents/d1/segment/start");
    expect(lastCall().init.method).toBe("POST");
  });
});
