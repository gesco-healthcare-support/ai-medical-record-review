import { describe, expect, it } from "vitest";
import {
  daysLeft,
  daysLeftLabel,
  deadlineNote,
  deadlineSortKey,
  deadlineTone,
  usDate,
} from "@/lib/deadlines";
import type { DocumentListItem } from "@/lib/types";

/** Synthetic records only. */
function doc(fields: Partial<DocumentListItem>): DocumentListItem {
  return {
    id: "a",
    original_filename: "record.pdf",
    page_count: 10,
    status: "uploaded",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    active_job: null,
    rows_count: 0,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
    ...fields,
  };
}

const TODAY = new Date(2026, 11, 20); // 12/20/2026, local time

describe("daysLeft", () => {
  it.each([
    ["2026-12-25", 5],
    ["2026-12-21", 1],
    ["2026-12-20", 0],
    ["2026-12-17", -3],
    ["2027-01-29", 40],
  ])("%s is %i days from 12/20/2026", (due, days) => {
    expect(daysLeft(due, TODAY)).toBe(days);
  });

  it("counts calendar days, whatever the time of day", () => {
    expect(daysLeft("2026-12-25", new Date(2026, 11, 20, 23, 59))).toBe(5);
    expect(daysLeft("2026-12-25", new Date(2026, 11, 20, 0, 1))).toBe(5);
  });
});

describe("daysLeftLabel", () => {
  it.each([
    [5, "5 days left"],
    [1, "1 day left"],
    [0, "Due today"],
    [-1, "1 day late"],
    [-3, "3 days late"],
  ])("%i reads %s", (days, label) => {
    expect(daysLeftLabel(days)).toBe(label);
  });
});

describe("usDate", () => {
  it("writes the date the office's way", () => {
    expect(usDate("2026-12-05")).toBe("12/05/2026");
  });
});

describe("deadlineTone", () => {
  it("is yellow for a record due on its appointment date", () => {
    expect(deadlineTone(doc({ visit_type: "QME", due_on: "2026-12-25" }))).toBe("appointment");
  });

  it("is light blue for late meds", () => {
    expect(deadlineTone(doc({ visit_type: "LATE", due_on: "2027-01-08" }))).toBe("late-meds");
  });

  it("leaves a supplemental report in the normal colour", () => {
    expect(deadlineTone(doc({ visit_type: "SUPP", due_on: "2027-01-08" }))).toBeNull();
  });

  it("leaves a record with no deadline, or marked Completed, uncoloured", () => {
    expect(deadlineTone(doc({ visit_type: "QME", due_on: "" }))).toBeNull();
    expect(deadlineTone(doc({}))).toBeNull();
    expect(
      deadlineTone(doc({ visit_type: "LATE", due_on: "2027-01-08", completed_at: "2026-12-01" })),
    ).toBeNull();
  });
});

describe("deadlineNote", () => {
  it("gives the days left, naming the kind of deadline when it is not an appointment", () => {
    expect(deadlineNote(doc({ visit_type: "QME", due_on: "2026-12-25" }), TODAY)).toBe("5 days left");
    expect(deadlineNote(doc({ visit_type: "LATE", due_on: "2026-12-30" }), TODAY)).toBe(
      "Late Meds - 10 days left",
    );
    expect(deadlineNote(doc({ visit_type: "SUPP", due_on: "2026-12-18" }), TODAY)).toBe(
      "SUPP - 2 days late",
    );
    expect(deadlineNote(doc({}), TODAY)).toBe("");
  });
});

describe("deadlineSortKey", () => {
  it("orders by deadline, closest first, records with none after, Completed ones last", () => {
    const records = [
      doc({ id: "none" }),
      doc({ id: "late", due_on: "2027-01-08" }),
      doc({ id: "done", due_on: "2026-12-01", completed_at: "2026-11-30" }),
      doc({ id: "soon", due_on: "2026-12-22" }),
    ];
    const order = [...records]
      .sort((a, b) => deadlineSortKey(a).localeCompare(deadlineSortKey(b)))
      .map((d) => d.id);
    expect(order).toEqual(["soon", "late", "none", "done"]);
  });
});
