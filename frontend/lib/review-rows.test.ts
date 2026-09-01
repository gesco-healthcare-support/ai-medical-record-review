import { describe, expect, it } from "vitest";

import {
  applyServerRowChanges,
  categoryWasGuessed,
  couldNotIdentify,
  mergeRows,
  newKey,
  rowErrors,
  sortRows,
  stripKeys,
  touchedFields,
  touchKey,
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

describe("couldNotIdentify", () => {
  // The rule is stated in issue #144: a document nothing identified is one that landed in General
  // (100) without a rule putting it there. Expected values come from that rule, not from the code.
  const general = (over: Partial<Row>): Row => ({ ...row(1, 2), category: "100", ...over });

  it("is true for a General row no rule claimed", () => {
    expect(couldNotIdentify(general({ ruled_paperwork: false }))).toBe(true);
  });

  it("is false when a rule named the title as paperwork", () => {
    expect(couldNotIdentify(general({ ruled_paperwork: true }))).toBe(false);
  });

  it("is true when a rule now sends the title elsewhere", () => {
    // ruled_paperwork is false for a rule answering any category other than 100. Such a row sits
    // in General only because it was segmented before that rule shipped, so it needs a look.
    const stale = general({ ruled_paperwork: false, title: "History and Physical" });
    expect(couldNotIdentify(stale)).toBe(true);
  });

  it("is false outside General, whatever the rule said", () => {
    expect(couldNotIdentify({ ...row(1, 2), category: "5", ruled_paperwork: false })).toBe(false);
    expect(couldNotIdentify({ ...row(1, 2), category: "5", ruled_paperwork: true })).toBe(false);
  });

  it("is true for a General row the server sent no verdict for", () => {
    // Rows the editor creates itself (insert, split) carry no verdict, and none of them is
    // paperwork a rule named - so the absent field must read as "not ruled", never as unknown.
    expect(couldNotIdentify(general({}))).toBe(true);
  });

  it("is false when both signals independently agreed it is paperwork", () => {
    // The only method that means General was a CONFIDENT answer rather than a guess (#188).
    expect(couldNotIdentify(general({ method: "llm+embedding" }))).toBe(false);
  });

  it("is true for every other method, including the ones that mean failure", () => {
    // llm-disagree is the population the reviewers actually asked to look through; timeout is a
    // row the categorization pool never finished, so nothing classified it at all.
    for (const method of [
      "llm-disagree",
      "llm-only",
      "embedding-only",
      "no-signal",
      "empty",
      "timeout",
    ]) {
      expect(couldNotIdentify(general({ method }))).toBe(true);
    }
  });

  it("is true when a rule decided at segment time but no longer claims the title", () => {
    // method "rules" is NOT excluded. It records that a rule fired when the row was segmented; if
    // that rule has since been narrowed, ruled_paperwork is false and nobody calls it paperwork
    // today - so it belongs in the list. Excluding on method here would hide it forever.
    expect(couldNotIdentify(general({ method: "rules", ruled_paperwork: false }))).toBe(true);
  });

  it("treats an absent or null method as unknown, never as confident", () => {
    // The regression guard for every row segmented before the column existed: they read null and
    // must keep behaving exactly as they did before #188 shipped.
    expect(couldNotIdentify(general({ method: null }))).toBe(true);
    expect(couldNotIdentify(general({ method: undefined }))).toBe(true);
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

describe("applyServerRowChanges with a touched set", () => {
  const local = (over: Partial<Row & { _key: string }> = {}): Row & { _key: string } => ({
    ...row(1, 3),
    _key: "k1",
    ...over,
  });

  it("keeps an unsaved re-classify instead of reverting it to the server's category", () => {
    // Measured on the previous fix: re-classifying a DIFFERENT summary on the Summaries tab fired
    // reloadRows and reverted this row's unsaved category, because the server value was taken
    // unconditionally for every span-matched row.
    const rows = [local({ category: "13" })]; // reviewer typed 13, not saved yet
    const server = [{ ...row(1, 3), category: "1" }];
    const touched = new Set([touchKey(rows[0], "category")]);
    expect(applyServerRowChanges(rows, server, touched)[0].category).toBe("13");
    // Without the touched entry the server still wins - that is the reload doing its job.
    expect(applyServerRowChanges(rows, server)[0].category).toBe("1");
  });

  it("keeps an unsaved untick instead of re-including the row", () => {
    const rows = [local({ include: false })]; // reviewer unticked "summarize", not saved yet
    const server = [{ ...row(1, 3), include: true }];
    const touched = new Set([touchKey(rows[0], "include")]);
    expect(applyServerRowChanges(rows, server, touched)[0].include).toBe(false);
  });

  it("protects only the touched field, so the other still tracks the server", () => {
    const rows = [local({ category: "13", include: true })];
    const server = [{ ...row(1, 3), category: "1", include: false }];
    const result = applyServerRowChanges(rows, server, new Set([touchKey(rows[0], "category")]));
    expect(result[0].category).toBe("13"); // the reviewer's
    expect(result[0].include).toBe(false); // the Duplicates tab's
  });

  it("protects one row without protecting its neighbour", () => {
    const rows = [
      local({ _key: "a", start: 1, end: 3, category: "13" }),
      local({ _key: "b", start: 4, end: 6, category: "13" }),
    ];
    const server = [
      { ...row(1, 3), category: "1" },
      { ...row(4, 6), category: "1" },
    ];
    const result = applyServerRowChanges(rows, server, new Set(["a:category"]));
    expect(result.map((r) => r.category)).toEqual(["13", "1"]);
  });
});

describe("touchedFields", () => {
  const r = (over: Partial<Row & { _key: string }> = {}): Row & { _key: string } => ({
    ...row(1, 3),
    _key: "k1",
    ...over,
  });

  it("reports the server-writable fields an edit changed", () => {
    expect(touchedFields([r()], [r({ category: "13" })])).toEqual(["k1:category"]);
    expect(touchedFields([r()], [r({ include: false })])).toEqual(["k1:include"]);
    expect(touchedFields([r()], [r({ category: "13", include: false })])).toEqual([
      "k1:include",
      "k1:category",
    ]);
  });

  it("reports nothing for an edit to a field the server never writes", () => {
    // Title/date collisions cannot happen, so tracking them would grow a set nothing reads.
    expect(touchedFields([r()], [r({ title: "New title", date: "01/01/2026" })])).toEqual([]);
  });

  it("reports nothing when an edit changes no value", () => {
    expect(touchedFields([r()], [r()])).toEqual([]);
  });

  it("ignores a row that did not exist before the edit", () => {
    // A split or insert mints a new _key; the row already carries the reviewer's values in full.
    expect(touchedFields([r({ _key: "a" })], [r({ _key: "a" }), r({ _key: "b" })])).toEqual([]);
  });

  it("follows the row's key, not its position", () => {
    const before = [r({ _key: "a", start: 4, end: 6 }), r({ _key: "b", start: 1, end: 3 })];
    const after = [r({ _key: "b", start: 1, end: 3 }), r({ _key: "a", start: 4, end: 6, include: false })];
    expect(touchedFields(before, after)).toEqual(["a:include"]);
  });
});

describe("categoryWasGuessed", () => {
  const shipping = (over: Partial<Row>): Row => ({ ...row(1, 2), category: "13", ...over });

  it("flags a row the cascade guessed onto a real category", () => {
    // Adam's EMG report, 2026-08-31: method=llm-disagree, category 13, included, summarized under
    // the evaluation point list. Nothing on screen said the category was a coin-flip.
    expect(categoryWasGuessed(shipping({ method: "llm-disagree" }))).toBe(true);
  });

  it("stays quiet when a rule decided the category", () => {
    expect(categoryWasGuessed(shipping({ method: "rules" }))).toBe(false);
  });

  it("stays quiet when both classifiers independently agreed", () => {
    expect(categoryWasGuessed(shipping({ method: "llm+embedding" }))).toBe(false);
  });

  it("flags every other method value", () => {
    for (const method of ["llm-disagree", "llm-only", "embedding-only", "no-signal", "empty", "timeout"]) {
      expect(categoryWasGuessed(shipping({ method }))).toBe(true);
    }
  });

  it("reads a MISSING method as not guessed - the opposite of couldNotIdentify, on purpose", () => {
    // Unknown must not be read as confident inside General, where the reviewer is already looking.
    // Here it would flag every row segmented before the column existed - thousands, none of them
    // evidence - and a filter matching everything surfaces nothing.
    expect(categoryWasGuessed(shipping({}))).toBe(false);
    expect(categoryWasGuessed(shipping({ method: null }))).toBe(false);
  });

  it("leaves General to couldNotIdentify, so a row never carries both chips", () => {
    const general = { ...row(1, 2), category: "100", method: "llm-disagree", ruled_paperwork: false };
    expect(categoryWasGuessed(general)).toBe(false);
    expect(couldNotIdentify(general)).toBe(true);
  });
});

describe("the two predicates are disjoint and neither drives the other", () => {
  it("a guessed shipping row is invisible to couldNotIdentify - which is why the chip exists", () => {
    // The regression this exists for: 51 of 82 low-confidence rows on the box landed on a shipping
    // category, 40 of them included for summary, and couldNotIdentify listed none of them.
    const guessed = { ...row(1, 2), category: "5", method: "llm-disagree" };
    expect(couldNotIdentify(guessed)).toBe(false);
    expect(categoryWasGuessed(guessed)).toBe(true);
  });

  it("a settled row is flagged by neither", () => {
    expect(couldNotIdentify({ ...row(1, 2), category: "13", method: "rules" })).toBe(false);
    expect(categoryWasGuessed({ ...row(1, 2), category: "13", method: "rules" })).toBe(false);
  });

  it("no row can carry both, because General belongs to couldNotIdentify alone", () => {
    for (const method of ["llm-disagree", "llm-only", "no-signal", "empty", "rules"]) {
      const general = { ...row(1, 2), category: "100", method, ruled_paperwork: false };
      expect(categoryWasGuessed(general)).toBe(false);
    }
  });
});

// #204. `_key` is not only a React key: `touchKey` builds the touched set out of it, and
// `applyServerRowChanges` reads that set to decide whether a reload may overwrite a field. The hook
// clears the set whenever it replaces the buffer wholesale, but keys that are unique for the
// lifetime of the page are the SECOND, independent reason a leftover entry cannot do damage - and
// that property was undocumented, which is what made the leak worth an issue rather than a shrug.
//
// So pin it here. Making keySeq per-document or per-call looks like a tidy way to get deterministic
// keys in tests; it would silently re-point a stale entry at a real row in the next document.
describe("client keys are unique for the lifetime of the module", () => {
  it("withKeys never re-mints a key it has already handed out", () => {
    const first = withKeys([row(1, 2), row(3, 4)]).map((r) => r._key);
    const second = withKeys([row(1, 2), row(3, 4)]).map((r) => r._key);
    expect(new Set([...first, ...second]).size).toBe(4);
  });

  it("newKey does not collide with withKeys, so a split cannot inherit a stale claim", () => {
    const before = withKeys([row(1, 2)])[0]._key;
    const inserted = newKey();
    const after = withKeys([row(3, 4)])[0]._key;
    expect(new Set([before, inserted, after]).size).toBe(3);
  });

  it("a key from an earlier batch cannot name a row in a later one", () => {
    // The failure mode stated as the set sees it: touchKey over two batches yields four distinct
    // entries, so document A's claim can never match document B's row.
    const a = withKeys([row(1, 2)])[0];
    const b = withKeys([row(1, 2)])[0];
    expect(touchKey(a, "category")).not.toBe(touchKey(b, "category"));
    expect(touchKey(a, "include")).not.toBe(touchKey(b, "include"));
  });
});
