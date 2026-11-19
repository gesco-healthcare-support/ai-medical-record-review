import { describe, expect, it } from "vitest";

import {
  guessKind,
  inReceivedOrder,
  RECEIVED_KIND_LABELS,
  RECEIVED_KINDS,
  RECORDS_FROM,
  RECORDS_FROM_LABELS,
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
