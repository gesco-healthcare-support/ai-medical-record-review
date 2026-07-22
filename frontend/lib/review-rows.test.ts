import { describe, expect, it } from "vitest";

import { newKey, rowErrors, sortRows, stripKeys, withKeys } from "@/lib/review-rows";
import type { Row } from "@/lib/types";

// Expected values are derived from the DOCUMENTED rule (mirrored server-side in
// app/services/rows.py): integer pages, 1 <= start <= end <= total, ascending, non-overlapping;
// gaps between documents are allowed on purpose. They are NOT read back off the implementation.
const row = (start: number, end: number): Row => ({
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

describe("rowErrors", () => {
  it("returns no errors for contiguous, in-range, ascending rows", () => {
    expect(rowErrors([row(1, 3), row(4, 6)], 6).size).toBe(0);
  });

  it("allows a gap between documents (skipped junk pages)", () => {
    // page 4 is skipped between [1-3] and [5-6] - a legal gap, not an error.
    expect(rowErrors([row(1, 3), row(5, 6)], 6).size).toBe(0);
  });

  it("flags an overlap with the previous document", () => {
    // second row start 3 <= previous end 3 -> overlap.
    expect(rowErrors([row(1, 3), row(3, 5)], 5).get(1)).toBe("overlaps the previous document");
  });

  it("treats start == previousEnd + 1 as valid (overlap boundary)", () => {
    expect(rowErrors([row(1, 3), row(4, 5)], 5).size).toBe(0);
  });

  it("flags start < 1", () => {
    expect(rowErrors([row(0, 2)], 5).get(0)).toBe("needs 1 <= start <= end <= 5");
  });

  it("flags end > totalPages", () => {
    expect(rowErrors([row(1, 6)], 5).get(0)).toBe("needs 1 <= start <= end <= 5");
  });

  it("flags start > end", () => {
    expect(rowErrors([row(4, 2)], 5).get(0)).toBe("needs 1 <= start <= end <= 5");
  });

  it("accepts end == totalPages and start == end (range boundaries)", () => {
    expect(rowErrors([row(5, 5)], 5).size).toBe(0);
  });

  it("flags a non-integer page as not a number", () => {
    expect(rowErrors([row(1.5, 2)], 5).get(0)).toBe("pages must be numbers");
  });

  it("collects errors for EVERY invalid row (client does not stop at the first)", () => {
    // Unlike the server twin (returns the first error), the client maps all of them for the editor.
    const errs = rowErrors([row(0, 2), row(6, 7)], 5); // row 0 start<1; row 1 end>5
    expect(errs.size).toBe(2);
    expect(errs.get(0)).toBe("needs 1 <= start <= end <= 5");
    expect(errs.get(1)).toBe("needs 1 <= start <= end <= 5");
  });

  it("advances previousEnd past a non-integer row so a later overlap still flags", () => {
    // row 0 has a non-integer start but an integer end (3), so previousEnd advances to 3;
    // row 1 starting at 2 then overlaps it.
    const errs = rowErrors([row(1.5, 3), row(2, 4)], 5);
    expect(errs.get(0)).toBe("pages must be numbers");
    expect(errs.get(1)).toBe("overlaps the previous document");
  });
});

describe("sortRows", () => {
  it("sorts by start then end without mutating the input", () => {
    const input = [row(5, 6), row(1, 3), row(1, 2)];
    const sorted = sortRows(input);
    expect(sorted.map((r) => [r.start, r.end])).toEqual([
      [1, 2],
      [1, 3],
      [5, 6],
    ]);
    expect(input.map((r) => r.start)).toEqual([5, 1, 1]); // original array untouched
  });
});

describe("withKeys / newKey / stripKeys", () => {
  it("assigns a unique _key to each row", () => {
    const keyed = withKeys([row(1, 2), row(3, 4)]);
    expect(new Set(keyed.map((r) => r._key)).size).toBe(2);
  });

  it("newKey returns a fresh key each call", () => {
    expect(newKey()).not.toBe(newKey());
  });

  it("stripKeys removes the client-only _key", () => {
    const stripped = stripKeys(withKeys([row(1, 2)]));
    expect(stripped.every((r) => !("_key" in r))).toBe(true);
  });
});
