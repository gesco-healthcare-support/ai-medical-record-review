import { describe, expect, it } from "vitest";
import { agreedPatientId, cleanPatientId, patientIdFromFilename } from "./patient-id";

// The same cases as backend/tests/test_patient_ids.py `_NAMES`: the two copies of the rule must
// give the same answer. Every name and number is synthetic.
const NAMES: [string, string | null][] = [
  ["Jane Example-801234-11032026.pdf", "801234"],
  ["Jane Example-AB1234-11032026.pdf", "AB1234"],
  ["Jane Example-K12345-11032026.pdf", "K12345"],
  ["Jane Example-1234567-11032026.pdf", "1234567"],
  ["801234 Jane Example 11-03-2026.pdf", "801234"],
  ["ab1234_Example_Jane.pdf", "AB1234"],
  ["Example, Jane 11.3.26 801234.PDF", "801234"],
  ["Jane Example 801234 801234.pdf", "801234"],
  ["Jane Example.pdf", null],
  ["Jane Example records 2026.pdf", null],
  ["Jane Example-11032026.pdf", null],
  ["Jane Example 110326.pdf", null],
  ["Jane Example 11-03-2026 p 12345.pdf", null],
  ["", null],
  ["Jane Example-801234-AB1234.pdf", null],
];

describe("patientIdFromFilename", () => {
  it.each(NAMES)("reads %j as %j", (name, expected) => {
    expect(patientIdFromFilename(name)).toBe(expected);
  });

  it("takes six digits that read as a date for the file date, never an ID starting 80", () => {
    expect(patientIdFromFilename("Jane Example 110326.pdf")).toBeNull();
    expect(patientIdFromFilename("Jane Example 801234.pdf")).toBe("801234");
    expect(patientIdFromFilename("Jane Example 139999.pdf")).toBe("139999");
  });
});

describe("agreedPatientId", () => {
  it("takes the ID the pieces agree on, ignoring pieces without one", () => {
    expect(agreedPatientId(["a-801234.pdf", "b-801234.pdf", "cover.pdf"])).toBe("801234");
    expect(agreedPatientId(["a-801234.pdf", "b-AB1234.pdf"])).toBeNull();
    expect(agreedPatientId(["cover.pdf"])).toBeNull();
  });
});

describe("cleanPatientId", () => {
  it("keeps letters and digits, upper-case", () => {
    expect(cleanPatientId(" ab-1234 ")).toBe("AB1234");
    expect(cleanPatientId("80 12 34")).toBe("801234");
    expect(cleanPatientId("   ")).toBe("");
  });
});
