import { describe, expect, it } from "vitest";

import {
  ACCEPTED_FILES,
  guessKind,
  inReceivedOrder,
  isAcceptedFile,
  RECEIVED_KIND_LABELS,
  RECEIVED_KINDS,
  RECORDS_FROM,
  RECORDS_FROM_LABELS,
  shownKind,
  type UploadedBefore,
  uploadedBeforeLine,
} from "@/lib/received-files";

describe("guessKind", () => {
  it.each([
    ["Synthetic_Patient email.pdf", "email"],
    ["FW records received - Outlook.pdf", "email"],
    ["advocacy letter.pdf", "advocacy_letter"],
    ["ADV LTR.pdf", "advocacy_letter"],
    ["DEC.pdf", "declaration"],
    ["Declaration of custodian.pdf", "declaration"],
    ["POS.pdf", "proof_of_service"],
    ["Proof of Service.pdf", "proof_of_service"],
    // A declaration OF SERVICE is a proof of service, not the declaration.
    ["Declaration of Service.pdf", "proof_of_service"],
    ["Medical Index.pdf", "medical_index"],
    ["envelope scan.pdf", "envelope"],
    // The office's cover sheet is no longer used (2026-11-19): a cover letter is the advocacy
    // letter, anything else named "cover" is just a file.
    ["cover letter.pdf", "advocacy_letter"],
    ["cover sheet.pdf", "records"],
    ["Hospital records part 2.pdf", "records"],
    ["scan0001.pdf", "records"],
  ])("guesses %s as %s", (name, kind) => {
    expect(guessKind(name)).toBe(kind);
  });

  it("does not read a kind out of the middle of a word", () => {
    // "pos" inside "position", "dec" inside "december", "ltr" inside nothing.
    expect(guessKind("position statement december.pdf")).toBe("records");
  });
});

describe("shownKind", () => {
  it("takes the server's sure reading over the file's name", () => {
    expect(shownKind({ kind: "records", check: false }, "records")).toEqual({
      kind: "records",
      check: false,
    });
    expect(shownKind({ kind: "proof_of_service", check: false }, "records")).toEqual({
      kind: "proof_of_service",
      check: false,
    });
  });

  it("asks for a look when a sure reading and the name disagree", () => {
    // A copy service's packet named "POS.pdf" that the model read as records: the name is a hint,
    // not an answer, so the reading stays and the uploader looks.
    expect(shownKind({ kind: "records", check: false }, "proof_of_service")).toEqual({
      kind: "records",
      check: true,
    });
  });

  it("uses the name only when an unsure reading says no more than records", () => {
    expect(shownKind({ kind: "records", check: true }, "declaration")).toEqual({
      kind: "declaration",
      check: true,
    });
    expect(shownKind({ kind: "email", check: true }, "declaration")).toEqual({
      kind: "email",
      check: true,
    });
  });
});

describe("isAcceptedFile", () => {
  const file = (name: string, type: string) => new File([new Uint8Array([1])], name, { type });

  it.each([
    ["letter.pdf", "application/pdf"],
    ["scan.jpg", "image/jpeg"],
    ["scan.TIFF", ""],
    ["fax.tif", "image/tiff"],
    ["page.png", "image/png"],
    ["records.PDF", ""],
  ])("takes %s", (name, type) => {
    expect(isAcceptedFile(file(name, type))).toBe(true);
  });

  it.each([
    ["notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ["mail.msg", "application/vnd.ms-outlook"],
    ["archive.zip", "application/zip"],
  ])("refuses %s", (name, type) => {
    expect(isAcceptedFile(file(name, type))).toBe(false);
  });

  it("offers the same types to the file picker", () => {
    for (const accepted of ["application/pdf", "image/tiff", ".tif", ".jpg", ".pdf"]) {
      expect(ACCEPTED_FILES.split(",")).toContain(accepted);
    }
  });
});

describe("inReceivedOrder", () => {
  it("puts the files in the order a record is put together, keeping the order within a kind", () => {
    const items = [
      { name: "r1", kind: "records" as const },
      { name: "d", kind: "declaration" as const },
      { name: "env", kind: "envelope" as const },
      { name: "r2", kind: "records" as const },
      { name: "e", kind: "email" as const },
      { name: "p", kind: "proof_of_service" as const },
      { name: "i", kind: "medical_index" as const },
      { name: "a", kind: "advocacy_letter" as const },
    ];
    expect(inReceivedOrder(items).map((item) => item.name)).toEqual([
      "a",
      "d",
      "i",
      "r1",
      "r2",
      "p",
      "e",
      "env",
    ]);
  });

  it("names every kind, and mirrors the server's order", () => {
    // backend/app/services/received_files.py KINDS - the server refuses any other kind.
    expect(RECEIVED_KINDS).toEqual([
      "advocacy_letter",
      "declaration",
      "medical_index",
      "records",
      "proof_of_service",
      "email",
      "envelope",
    ]);
    expect(Object.keys(RECEIVED_KIND_LABELS).sort()).toEqual([...RECEIVED_KINDS].sort());
    // received_files.PARTIES - the server refuses any other.
    expect(RECORDS_FROM).toEqual(["defense", "applicant", "insurance", "copy_service"]);
    expect(Object.keys(RECORDS_FROM_LABELS).sort()).toEqual([...RECORDS_FROM].sort());
  });
});

describe("uploadedBeforeLine", () => {
  // Noon UTC: the same day in every US time zone.
  const at = (overrides: Partial<UploadedBefore> = {}): UploadedBefore => ({
    uploaded_at: "2026-11-24T12:00:00+00:00",
    pages: 12,
    in_team_queue: false,
    yours: false,
    reviewer: "Brian",
    part_of_record: false,
    ...overrides,
  });

  it("says nothing for a new file", () => {
    expect(uploadedBeforeLine([])).toBe("");
  });

  it("says when, where and how big the record is", () => {
    expect(uploadedBeforeLine([at()])).toBe(
      "Already uploaded on 11/24/2026, in Brian's records (12 pages).",
    );
    expect(uploadedBeforeLine([at({ in_team_queue: true, reviewer: "Brian" })])).toBe(
      "Already uploaded on 11/24/2026, in the team queue (12 pages).",
    );
    expect(uploadedBeforeLine([at({ yours: true })])).toBe(
      "Already uploaded on 11/24/2026, in your records (12 pages).",
    );
    expect(uploadedBeforeLine([at({ reviewer: "" })])).toBe(
      "Already uploaded on 11/24/2026, in another reviewer's records (12 pages).",
    );
  });

  it("says when the file is only part of a record, and counts pages plainly", () => {
    expect(uploadedBeforeLine([at({ part_of_record: true, pages: 1 })])).toBe(
      "Already uploaded on 11/24/2026 as part of a record, in Brian's records (1 page).",
    );
    expect(uploadedBeforeLine([at({ pages: 4120 })])).toBe(
      "Already uploaded on 11/24/2026, in Brian's records (4,120 pages).",
    );
  });

  it("names the latest of several, and how often - five or more past the server's five", () => {
    const older = at({ uploaded_at: "2026-11-02T12:00:00+00:00" });
    expect(uploadedBeforeLine([at(), older])).toBe(
      "Already uploaded on 11/24/2026, in Brian's records (12 pages). " +
        "Uploaded 2 times before; that is the latest.",
    );
    expect(uploadedBeforeLine([at(), older, older, older, older])).toMatch(
      / Uploaded 5 or more times before; that is the latest\.$/,
    );
  });
});
