import { describe, expect, it } from "vitest";
import { findSummaries, orderSummaries, summaryDateValue } from "@/lib/summary-order";
import type { SummaryItem } from "@/lib/types";

const item = (idx: number, summaryDate: string, summaryTitle = "", summaryText = "") =>
  ({ idx, summaryDate, summaryTitle, summaryText }) as unknown as SummaryItem;

describe("summaryDateValue", () => {
  it("reads the date shapes a summary carries", () => {
    expect(summaryDateValue("09/22/26")).toBe(20260922);
    expect(summaryDateValue("09/22/2026")).toBe(20260922);
    expect(summaryDateValue("9-2-2026")).toBe(20260902);
    expect(summaryDateValue("12.04.25")).toBe(20251204);
  });

  it("puts a two-digit year in the century the exported document does", () => {
    expect(summaryDateValue("01/01/68")).toBe(20680101);
    expect(summaryDateValue("01/01/69")).toBe(19690101);
  });

  it("treats anything that is not a date as undated rather than guessing", () => {
    for (const value of ["", "-", "Undated", "no date", "13/01/2026", "02/32/2026", "2026-09-22"]) {
      expect(summaryDateValue(value)).toBeNull();
    }
  });
});

describe("findSummaries", () => {
  const items = [
    item(0, "01/05/26", "DR. SMITH. MRI OF THE LUMBAR SPINE", "Impression: disc bulge."),
    item(1, "02/10/26", "DR. JONES. PROGRESS REPORT", "Work status: modified duty."),
    item(2, "03/15/26", "DR. SMITH. PROGRESS REPORT", "Follow up in six weeks."),
  ];

  it("keeps everything for an empty search", () => {
    expect(findSummaries(items, "  ")).toBe(items);
  });

  it("matches the title, the text and the date, ignoring case", () => {
    expect(findSummaries(items, "jones").map((i) => i.idx)).toEqual([1]);
    expect(findSummaries(items, "MODIFIED").map((i) => i.idx)).toEqual([1]);
    expect(findSummaries(items, "03/15").map((i) => i.idx)).toEqual([2]);
  });

  it("needs every word, so two words narrow the list rather than widen it", () => {
    expect(findSummaries(items, "smith progress").map((i) => i.idx)).toEqual([2]);
  });
});

describe("orderSummaries", () => {
  const items = [
    item(0, "03/15/26"),
    item(1, "-"),
    item(2, "01/05/26"),
    item(3, "03/15/26"),
    item(4, "02/10/2026"),
  ];

  it("leaves page order alone", () => {
    expect(orderSummaries(items, "pages")).toBe(items);
  });

  it("puts the oldest first, undated last, and keeps page order on a shared date", () => {
    expect(orderSummaries(items, "date").map((i) => i.idx)).toEqual([2, 4, 0, 3, 1]);
  });

  it("does not reorder the list it was given", () => {
    orderSummaries(items, "date");
    expect(items.map((i) => i.idx)).toEqual([0, 1, 2, 3, 4]);
  });
});
