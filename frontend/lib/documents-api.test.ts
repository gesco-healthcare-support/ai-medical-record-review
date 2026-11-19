/**
 * The documents client calls.
 *
 * Two of these send `FormData`, and the header behaviour around that is the whole reason this file
 * stubs `fetch` rather than `apiFetch`: the guarantee worth having is about a header that `apiFetch`
 * deliberately does NOT set, and stubbing one layer higher would hide it entirely.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  combineReceivedFiles,
  deleteDocument,
  downloadRawPdf,
  listDocuments,
  rawPdfUrl,
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

  it("sends the received files in order, each with its kind, and the patient's details", async () => {
    const files = [pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")];
    await combineReceivedFiles({
      lastName: "  Synthetic ",
      firstName: " Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      appointmentDate: "2026-12-01",
      recordsFrom: "insurance",
      files: [
        { file: files[0], kind: "email" },
        { file: files[1], kind: "advocacy_letter" },
        { file: files[2], kind: "records" },
      ],
    });

    const { url, init } = lastCall();
    expect(url).toBe("/api/documents/combine");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    // getAll, not get: a loop that overwrote instead of appending would upload only the last file
    // and lose the rest without any error. Files and kinds pair up by position.
    expect(body.getAll("pdfs")).toEqual(files);
    expect(body.getAll("kinds")).toEqual(["email", "advocacy_letter", "records"]);
    expect(body.get("last_name")).toBe("Synthetic");
    expect(body.get("first_name")).toBe("Patient");
    expect(body.get("patient_id")).toBe("801234");
    expect(body.get("date_received")).toBe("2026-11-03");
    expect(body.get("appointment_date")).toBe("2026-12-01");
    expect(body.get("records_from")).toBe("insurance");
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });
});

describe("the raw PDF", () => {
  it("is fetched by an ordinary link the browser downloads", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      downloadRawPdf("d 1");
      expect(click).toHaveBeenCalledTimes(1);
      const link = click.mock.contexts[0] as HTMLAnchorElement;
      expect(link.getAttribute("href")).toBe(rawPdfUrl("d 1"));
      expect(rawPdfUrl("d 1")).toBe("/api/documents/d%201/raw");
      expect(link.hasAttribute("download")).toBe(true);
      // Nothing is read into the page: no fetch, no Blob (#389).
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      click.mockRestore();
    }
  });
});

describe("listing another reviewer's records", () => {
  it("asks for them by owner, and for your own with no owner", async () => {
    // The admin picker sends the chosen reviewer; the server decides whether to honour it.
    await listDocuments(7);
    expect(lastCall().url).toBe("/api/documents?owner=7");
    await listDocuments(null);
    expect(lastCall().url).toBe("/api/documents");
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

describe("uploading into another reviewer's account", () => {
  it("sends the reviewer as `owner`, and confirms a duplicate only when asked", async () => {
    await uploadDocument(pdf(), { owner: 7 });
    let body = lastCall().init.body as FormData;
    expect(body.get("owner")).toBe("7");
    expect(body.has("allow_duplicate")).toBe(false);

    await uploadDocument(pdf(), { owner: 7, allowDuplicate: true });
    body = lastCall().init.body as FormData;
    expect(body.get("allow_duplicate")).toBe("true");
  });

  it("sends neither field on an ordinary upload", async () => {
    await uploadDocument(pdf());
    const body = lastCall().init.body as FormData;
    expect(body.has("owner")).toBe(false);
    expect(body.has("allow_duplicate")).toBe(false);
  });

  it("sends the reviewer on a received-files upload too, and nothing when there is none", async () => {
    const upload = {
      lastName: "Synthetic",
      firstName: "Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      appointmentDate: "2026-12-01",
      recordsFrom: "defense" as const,
      files: [{ file: pdf("a.pdf"), kind: "records" as const }],
    };
    await combineReceivedFiles({ ...upload, owner: 7 });
    expect((lastCall().init.body as FormData).get("owner")).toBe("7");
    await combineReceivedFiles(upload);
    expect((lastCall().init.body as FormData).has("owner")).toBe(false);
  });
});
