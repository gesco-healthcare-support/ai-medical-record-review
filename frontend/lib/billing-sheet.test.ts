import { describe, expect, it } from "vitest";
import {
  type BillingSheet,
  billingReady,
  canDownload,
  EMPTY_BILLING,
  editValid,
  isPageCount,
  REVIEW_SOURCE_LABELS,
  REVIEW_SOURCES,
  shownCounts,
  toEdit,
  VISIT_TYPES,
} from "@/lib/billing-sheet";

const READY = {
  ...EMPTY_BILLING,
  visit_type: "QME",
  appointment_date: "2026-12-01",
  review_source: "edata",
  interpreter: "no",
};

const SHEET: BillingSheet = {
  visit_type: "QME",
  appointment_date: "2026-12-01",
  doctor: "Alpha",
  review_source: "in_house",
  interpreter: "yes",
  claim_numbers: "ADJ1",
  previous_visit: "",
  declared_pages: 40,
  discrepancy_reason: "",
  records_from: "defense",
  received_on: "2026-11-19",
  office_count: "",
  depo_pages: "",
  counted: { office: 42, depo: 7 },
  pages_counted: 42,
  pages_unread: 0,
  doctors: ["Alpha"],
};

describe("the choices", () => {
  it("are the office's own, SUPP last", () => {
    expect(VISIT_TYPES).toEqual(["AME", "QME", "PQME", "IME", "SUPP"]);
    expect(REVIEW_SOURCES.map((source) => REVIEW_SOURCE_LABELS[source])).toEqual([
      "E-DATA",
      "IN-HOUSE",
      "LEONARDO",
    ]);
  });
});

describe("isPageCount", () => {
  it.each([
    ["", true],
    [" 40 ", true],
    ["0", true],
    ["six", false],
    ["-3", false],
    ["1.5", false],
  ])("%j is %s", (value, ok) => {
    expect(isPageCount(value)).toBe(ok);
  });
});

describe("billingReady", () => {
  it("needs the type of visit, the appointment date, who reviews and the interpreter", () => {
    expect(billingReady(READY)).toBe(true);
    for (const field of ["visit_type", "appointment_date", "review_source", "interpreter"]) {
      expect(billingReady({ ...READY, [field]: "" })).toBe(false);
    }
  });

  it("needs no appointment date for a supplemental report", () => {
    expect(billingReady({ ...READY, visit_type: "SUPP", appointment_date: "" })).toBe(true);
  });

  it("refuses a declared page count that is not a whole number, and overlong claim numbers", () => {
    expect(billingReady({ ...READY, declared_pages: "six" })).toBe(false);
    expect(billingReady({ ...READY, claim_numbers: "x".repeat(256) })).toBe(false);
    expect(billingReady({ ...READY, declared_pages: "6", claim_numbers: "x".repeat(255) })).toBe(
      true,
    );
  });
});

describe("the sheet's edit", () => {
  it("holds every field as text, counts included", () => {
    expect(toEdit(SHEET)).toEqual({
      visit_type: "QME",
      appointment_date: "2026-12-01",
      doctor: "Alpha",
      review_source: "in_house",
      interpreter: "yes",
      claim_numbers: "ADJ1",
      previous_visit: "",
      declared_pages: "40",
      discrepancy_reason: "",
      records_from: "defense",
      office_count: "",
      depo_pages: "",
    });
  });

  it("is valid with whole-number counts and texts within the limit", () => {
    const edit = toEdit(SHEET);
    expect(editValid(edit)).toBe(true);
    expect(editValid({ ...edit, office_count: "many" })).toBe(false);
    expect(editValid({ ...edit, depo_pages: "1.5" })).toBe(false);
    expect(editValid({ ...edit, declared_pages: "x" })).toBe(false);
    expect(editValid({ ...edit, discrepancy_reason: "x".repeat(256) })).toBe(false);
  });
});

describe("the counts the sheet prints", () => {
  it("are the worked-out ones, unless typed over", () => {
    expect(shownCounts(SHEET)).toEqual({ office: 42, depo: 7 });
    expect(shownCounts({ ...SHEET, office_count: 50, depo_pages: 0 })).toEqual({
      office: 50,
      depo: 0,
    });
  });

  it("are unknown while the pages are read, and the sheet cannot be downloaded", () => {
    const reading = { ...SHEET, counted: null, pages_unread: 10 };
    expect(shownCounts(reading)).toEqual({ office: null, depo: null });
    expect(canDownload(reading)).toBe(false);
    expect(canDownload({ ...reading, office_count: 9 })).toBe(false);
    expect(canDownload({ ...reading, office_count: 9, depo_pages: 0 })).toBe(true);
    expect(canDownload(SHEET)).toBe(true);
  });
});
