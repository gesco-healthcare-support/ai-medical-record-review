import { describe, expect, it } from "vitest";
import {
  type BillingSheet,
  billingReady,
  depoPending,
  EMPTY_BILLING,
  editValid,
  isPageCount,
  isPageCountList,
  shownCounts,
  takesAppointment,
  toEdit,
  VISIT_TYPES,
  visitLabel,
} from "@/lib/billing-sheet";

const READY = {
  ...EMPTY_BILLING,
  visit_type: "QME",
  appointment_date: "2026-12-01",
  interpreter: "no",
};

const SHEET: BillingSheet = {
  visit_type: "QME",
  appointment_date: "2026-12-01",
  doctor: "Alpha",
  interpreter: "yes",
  claim_numbers: "ADJ1",
  location: "Main office",
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
  it("are the office's own, SUPP and Late Meds last", () => {
    expect(VISIT_TYPES).toEqual(["AME", "QME", "PQME", "IME", "SUPP", "LATE"]);
  });

  it("show Late Meds by its name and every other visit as stored", () => {
    expect(VISIT_TYPES.map(visitLabel)).toEqual(["AME", "QME", "PQME", "IME", "SUPP", "Late Meds"]);
  });

  it("take an appointment date except a supplemental report and late meds", () => {
    expect(VISIT_TYPES.filter((visit) => !takesAppointment(visit))).toEqual(["SUPP", "LATE"]);
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
  it("needs the type of visit, the appointment date and the interpreter", () => {
    // Who reviews the records is not asked (2026-11-20): every record is reviewed in house.
    expect(billingReady(READY)).toBe(true);
    for (const field of ["visit_type", "appointment_date", "interpreter"]) {
      expect(billingReady({ ...READY, [field]: "" })).toBe(false);
    }
  });

  it("needs no appointment date for a supplemental report or late meds", () => {
    expect(billingReady({ ...READY, visit_type: "SUPP", appointment_date: "" })).toBe(true);
    expect(billingReady({ ...READY, visit_type: "LATE", appointment_date: "" })).toBe(true);
  });

  it("refuses a declared page count that is not a whole number, and overlong texts", () => {
    expect(billingReady({ ...READY, declared_pages: "six" })).toBe(false);
    expect(billingReady({ ...READY, claim_numbers: "x".repeat(256) })).toBe(false);
    expect(billingReady({ ...READY, location: "x".repeat(256) })).toBe(false);
    expect(billingReady({ ...READY, location: "x".repeat(255) })).toBe(true);
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
      interpreter: "yes",
      claim_numbers: "ADJ1",
      location: "Main office",
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
    expect(editValid({ ...edit, depo_pages: "120, 85" })).toBe(true);
    expect(editValid({ ...edit, depo_pages: "120 and 85" })).toBe(false);
    expect(editValid({ ...edit, declared_pages: "x" })).toBe(false);
    expect(editValid({ ...edit, discrepancy_reason: "x".repeat(256) })).toBe(false);
    expect(editValid({ ...edit, location: "x".repeat(256) })).toBe(false);
  });
});

describe("isPageCountList", () => {
  it.each([
    ["", true],
    ["120", true],
    ["120, 85", true],
    ["120,85,4", true],
    [" 120 , 85 , ", true],
    ["120 85", false],
    ["120; 85", false],
    ["1.5", false],
    ["-3", false],
    [Array(60).fill("100").join(", "), false],
  ])("%j is %s", (value, ok) => {
    expect(isPageCountList(value)).toBe(ok);
  });
});

describe("the counts the sheet prints", () => {
  it("are the worked-out ones, unless typed over", () => {
    expect(shownCounts(SHEET)).toEqual({ office: 42, depo: "7" });
    expect(shownCounts({ ...SHEET, office_count: 50, depo_pages: "0" })).toEqual({
      office: 50,
      depo: "0",
    });
  });

  it("print several typed deposition counts as typed", () => {
    expect(shownCounts({ ...SHEET, depo_pages: "120, 85" }).depo).toBe("120, 85");
  });

  it("have the office count at once and the deposition pages once the pages are read", () => {
    const reading = { ...SHEET, counted: { office: 42, depo: null }, pages_unread: 10 };
    expect(shownCounts(reading)).toEqual({ office: 42, depo: null });
    expect(depoPending(reading)).toBe(true);
    expect(depoPending({ ...reading, depo_pages: "0" })).toBe(false);
    expect(depoPending(SHEET)).toBe(false);
  });

  it("from an older server, are both unknown until every page is read", () => {
    const older = { ...SHEET, counted: null, pages_unread: 10 };
    expect(shownCounts(older)).toEqual({ office: null, depo: null });
    expect(depoPending(older)).toBe(true);
  });
});
