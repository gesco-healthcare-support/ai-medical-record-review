import { fc, test } from "@fast-check/vitest";
import { describe, expect } from "vitest";

import { rowErrors } from "@/lib/review-rows";
import type { Row } from "@/lib/types";

// Property-based invariants for rowErrors. It shares the range + overlap + gaps-allowed rule with
// the server (app/services/rows.py validate_rows; see backend/tests/test_rows_property.py).
// Documented differences (intentionally NOT full parity): the client REJECTS non-integers while the
// server coerces via int(); the client collects ALL errors while the server returns the first; and
// category membership is server-only. fast-check shrinks any failure to a minimal counterexample.
const mkRow = (start: number, end: number): Row => ({
  start,
  end,
  category: "1",
  title: "",
  date: "",
  injury_date: "",
  flag: "-",
  suggest_merge: false,
  include: true,
});

// A legal rowset: ascending, non-overlapping integer page ranges within [1, total]; gaps allowed.
const validScenario = fc.integer({ min: 1, max: 300 }).chain((total) =>
  fc
    .array(
      fc.record({ gap: fc.integer({ min: 0, max: 5 }), len: fc.integer({ min: 0, max: 40 }) }),
      { maxLength: 8 },
    )
    .map((segments) => {
      const rows: Row[] = [];
      let cursor = 1;
      for (const { gap, len } of segments) {
        const start = cursor + gap; // optional leading gap
        if (start > total) break;
        const end = Math.min(start + len, total);
        rows.push(mkRow(start, end));
        cursor = end + 1;
      }
      return { rows, total };
    }),
);

describe("rowErrors invariants", () => {
  test.prop([validScenario])("a legal rowset yields no errors", ({ rows, total }) => {
    expect(rowErrors(rows, total).size).toBe(0);
  });

  test.prop([
    fc.integer({ min: 2, max: 60 }),
    fc.integer({ min: 1, max: 60 }),
    fc.integer({ min: 60, max: 300 }),
  ])(
    "a row starting at or before the previous end is flagged as an overlap",
    (firstEnd, start2, total) => {
      fc.pre(start2 <= firstEnd); // start2 <= previousEnd triggers the overlap rule
      const errs = rowErrors([mkRow(1, firstEnd), mkRow(start2, firstEnd)], total);
      expect(errs.get(1)).toBe("overlaps the previous document");
    },
  );

  test.prop([
    fc.integer({ min: 1, max: 60 }),
    fc.integer({ min: 1, max: 20 }),
    fc.integer({ min: 0, max: 40 }),
  ])("a gap between documents never causes an error", (firstEnd, gap, secondLen) => {
    const start2 = firstEnd + gap + 1; // strictly after the gap -> no overlap
    const end2 = start2 + secondLen;
    expect(rowErrors([mkRow(1, firstEnd), mkRow(start2, end2)], end2 + 5).size).toBe(0);
  });

  test.prop([
    fc.integer({ min: 1, max: 200 }),
    fc.constantFrom("startBelowOne", "endAboveTotal", "startAfterEnd"),
  ])("an out-of-range row is flagged with the range message", (total, mode) => {
    const row =
      mode === "startBelowOne"
        ? mkRow(0, Math.min(2, total))
        : mode === "endAboveTotal"
          ? mkRow(1, total + 1)
          : mkRow(2, 1); // start > end
    expect(rowErrors([row], total).get(0)).toBe(`needs 1 <= start <= end <= ${total}`);
  });

  test.prop([fc.integer({ min: 1, max: 40 })])(
    "a non-integer page is flagged as not a number",
    (base) => {
      // base + 0.5 is never an integer; the total is generous so the range rule is not what trips.
      expect(rowErrors([mkRow(base + 0.5, base + 2)], base + 5).get(0)).toBe(
        "pages must be numbers",
      );
    },
  );
});
