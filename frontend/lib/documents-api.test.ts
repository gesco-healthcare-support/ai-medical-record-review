/**
 * The documents client calls.
 *
 * Two of these send `FormData`, and the header behaviour around that is the whole reason this file
 * stubs `fetch` rather than `apiFetch`: the guarantee worth having is about a header that `apiFetch`
 * deliberately does NOT set, and stubbing one layer higher would hide it entirely.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  billingSheetUrl,
  combineReceivedFiles,
  deleteDocument,
  downloadBillingSheet,
  downloadRawPdf,
  getBillingSheet,
  listDoctors,
  listDocuments,
  rawPdfUrl,
  saveBillingSheet,
  stageReceivedFile,
  unstageReceivedFile,
  startIdentification,
  uploadDocument,
} from "@/lib/documents-api";
import { type BillingDraft, type BillingSheetEdit, EMPTY_BILLING } from "@/lib/billing-sheet";

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

const BILLING: BillingDraft = {
  ...EMPTY_BILLING,
  visit_type: "QME",
  appointment_date: "2026-12-01",
  review_source: "in_house",
  interpreter: "no",
};

const UPLOAD = {
  lastName: "Synthetic",
  firstName: "Patient",
  patientId: "801234",
  dateReceived: "2026-11-03",
  recordsFrom: "insurance" as const,
  files: [{ token: "t1", kind: "records" as const }],
};

function toEditForTest(): BillingSheetEdit {
  return { ...BILLING, discrepancy_reason: "", records_from: "", office_count: "", depo_pages: "" };
}

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

  it("sends one received file on its own, to be kept and read", async () => {
    const file = pdf("a.pdf");
    await stageReceivedFile(file);

    const { url, init } = lastCall();
    expect(url).toBe("/api/documents/combine/files");
    expect(init.method).toBe("POST");
    expect((init.body as FormData).get("pdf")).toBe(file);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("drops a staged file by its token", async () => {
    await unstageReceivedFile("abc_DEF-1");
    expect(lastCall().url).toBe("/api/documents/combine/files/abc_DEF-1");
    expect(lastCall().init.method).toBe("DELETE");
  });

  it("sends the staged files in order, each with its kind, and the patient's details", async () => {
    await combineReceivedFiles({
      lastName: "  Synthetic ",
      firstName: " Patient",
      patientId: "801234",
      dateReceived: "2026-11-03",
      recordsFrom: "insurance",
      billing: { ...BILLING, claim_numbers: " ADJ0000001 " },
      files: [
        { token: "t1", kind: "email" },
        { token: "t2", kind: "advocacy_letter" },
        { token: "t3", kind: "records" },
      ],
    });

    const { url, init } = lastCall();
    expect(url).toBe("/api/documents/combine");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    // getAll, not get: a loop that overwrote instead of appending would send only the last file
    // and lose the rest without any error. Tokens and kinds pair up by position.
    expect(body.getAll("tokens")).toEqual(["t1", "t2", "t3"]);
    expect(body.has("pdfs")).toBe(false);
    expect(body.getAll("kinds")).toEqual(["email", "advocacy_letter", "records"]);
    expect(body.get("last_name")).toBe("Synthetic");
    expect(body.get("first_name")).toBe("Patient");
    expect(body.get("patient_id")).toBe("801234");
    expect(body.get("date_received")).toBe("2026-11-03");
    expect(body.get("appointment_date")).toBe("2026-12-01");
    expect(body.get("records_from")).toBe("insurance");
    expect(body.get("visit_type")).toBe("QME");
    expect(body.get("review_source")).toBe("in_house");
    expect(body.get("interpreter")).toBe("no");
    expect(body.get("claim_numbers")).toBe("ADJ0000001");
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("leaves out the optional billing fields left blank", async () => {
    await combineReceivedFiles({ ...UPLOAD, billing: BILLING });

    const body = lastCall().init.body as FormData;
    for (const name of ["doctor", "claim_numbers", "previous_visit", "declared_pages"]) {
      expect(body.has(name)).toBe(false);
    }
  });

  it("sends the optional billing fields when given", async () => {
    await combineReceivedFiles({
      ...UPLOAD,
      billing: {
        ...BILLING,
        doctor: "Alpha",
        previous_visit: "2026-06-14",
        declared_pages: " 40 ",
      },
    });

    const body = lastCall().init.body as FormData;
    expect(body.get("doctor")).toBe("Alpha");
    expect(body.get("previous_visit")).toBe("2026-06-14");
    expect(body.get("declared_pages")).toBe("40");
  });

  it("sends no appointment date for a supplemental report", async () => {
    // The office's answer: a supplemental report has no appointment; the sheet's DOS says SUPP.
    await combineReceivedFiles({ ...UPLOAD, billing: { ...BILLING, visit_type: "SUPP" } });

    const body = lastCall().init.body as FormData;
    expect(body.get("visit_type")).toBe("SUPP");
    expect(body.has("appointment_date")).toBe(false);
  });
});

describe("the billing sheet", () => {
  it("reads and saves the sheet, and lists the doctors", async () => {
    await getBillingSheet("d 1");
    expect(lastCall().url).toBe("/api/documents/d%201/billing");

    await saveBillingSheet("d1", { ...toEditForTest(), visit_type: "AME" });
    expect(lastCall().url).toBe("/api/documents/d1/billing");
    expect(lastCall().init.method).toBe("PUT");
    expect(JSON.parse(lastCall().init.body as string)).toMatchObject({ visit_type: "AME" });

    await listDoctors();
    expect(lastCall().url).toBe("/api/documents/combine/doctors");
  });

  it("is downloaded by an ordinary link the browser saves", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      downloadBillingSheet("d 1");
      const link = click.mock.contexts[0] as HTMLAnchorElement;
      expect(link.getAttribute("href")).toBe("/api/documents/d%201/billing-sheet");
      expect(billingSheetUrl("d1")).toBe("/api/documents/d1/billing-sheet");
      expect(link.hasAttribute("download")).toBe(true);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      click.mockRestore();
    }
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
      recordsFrom: "defense" as const,
      billing: BILLING,
      files: [{ token: "t1", kind: "records" as const }],
    };
    await combineReceivedFiles({ ...upload, owner: 7 });
    expect((lastCall().init.body as FormData).get("owner")).toBe("7");
    await combineReceivedFiles(upload);
    expect((lastCall().init.body as FormData).has("owner")).toBe(false);
  });
});
