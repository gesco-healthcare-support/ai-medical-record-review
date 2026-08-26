import { describe, expect, it } from "vitest";

import {
  applyServerRowChanges,
  mergeRows,
  newKey,
  rowErrors,
  sortRows,
  stripKeys,
  withKeys,
} from "@/lib/review-rows";
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

describe("mergeRows", () => {
  const r = (over: Partial<Row>): Row => ({ ...row(1, 3), ...over });

  it("takes the span of both halves and the upper row's identity", () => {
    const merged = mergeRows(
      r({ start: 1, end: 3, category: "13", title: "Upper", date: "01/02/2026" }),
      r({ start: 4, end: 9, category: "1", title: "Lower", date: "05/06/2026" }),
    );
    expect([merged.start, merged.end]).toEqual([1, 9]);
    // Identity comes from the upper row on purpose - "this continues the document above".
    expect([merged.category, merged.title, merged.date]).toEqual(["13", "Upper", "01/02/2026"]);
  });

  it("keeps the merged document in the deliverable when EITHER half was in it", () => {
    // The reported failure: a 2-page cover sheet in category 100 (include:false by default) sitting
    // above a 52-page evaluation. Merging used to hand the merged row the cover sheet's `include`,
    // so the evaluation stopped being summarized and nothing on screen said which document went.
    const merged = mergeRows(
      r({ start: 92, end: 93, category: "100", include: false }),
      r({ start: 94, end: 145, category: "13", include: true }),
    );
    expect(merged.include).toBe(true);
    // ...and the other way round, since a bulk apply merges in page order either way.
    expect(mergeRows(r({ include: true }), r({ include: false })).include).toBe(true);
  });

  it("only excludes the merged document when NEITHER half was included", () => {
    expect(mergeRows(r({ include: false }), r({ include: false })).include).toBe(false);
  });

  it("carries the manual-check flag across, as before", () => {
    expect(mergeRows(r({ flag: "-" }), r({ flag: "x" })).flag).toBe("x");
    expect(mergeRows(r({ flag: "x" }), r({ flag: "-" })).flag).toBe("x");
    expect(mergeRows(r({ flag: "-" }), r({ flag: "-" })).flag).toBe("-");
  });

  it("does not shrink the merged row when the lower half ends earlier", () => {
    expect(mergeRows(r({ start: 1, end: 9 }), r({ start: 4, end: 6 })).end).toBe(9);
  });
});

describe("applyServerRowChanges", () => {
  const local = (over: Partial<Row & { _key: string }> = {}): Row & { _key: string } => ({
    ...row(1, 3),
    _key: "k1",
    ...over,
  });

  it("takes include and category from the server for a row with the same span", () => {
    const result = applyServerRowChanges(
      [local({ start: 1, end: 3, include: true, category: "1" })],
      [{ ...row(1, 3), include: false, category: "5" }],
    );
    expect(result[0].include).toBe(false);
    expect(result[0].category).toBe("5");
  });

  it("keeps every local edit the reviewer has not saved yet", () => {
    // This is the whole point: the Duplicates tab wrote `include`, and the reviewer's retitle and
    // re-date are only in this buffer. Replacing it wholesale is what discarded them.
    const result = applyServerRowChanges(
      [local({ title: "Reviewer's title", date: "09/09/2026", injury_date: "01/01/2020" })],
      [{ ...row(1, 3), title: "", date: "", injury_date: "", include: false }],
    );
    expect(result[0].title).toBe("Reviewer's title");
    expect(result[0].date).toBe("09/09/2026");
    expect(result[0].injury_date).toBe("01/01/2020");
    expect(result[0]._key).toBe("k1"); // the client key survives, so React keeps input focus
  });

  it("leaves a row the reviewer has re-spanned entirely alone", () => {
    // No server twin: the reviewer has redefined this document, so their split is the newer fact.
    const result = applyServerRowChanges(
      [local({ start: 1, end: 2, include: true })],
      [{ ...row(1, 3), include: false }],
    );
    expect(result[0].include).toBe(true);
    expect([result[0].start, result[0].end]).toEqual([1, 2]);
  });

  it("keeps the local row set - it never adds or drops rows", () => {
    const result = applyServerRowChanges(
      [local({ _key: "a", start: 1, end: 2 }), local({ _key: "b", start: 3, end: 4 })],
      [{ ...row(1, 9) }],
    );
    expect(result.map((r) => r._key)).toEqual(["a", "b"]);
  });
});
