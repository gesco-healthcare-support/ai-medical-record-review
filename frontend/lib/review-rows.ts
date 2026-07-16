import type { Row } from "@/lib/types";

/** A row plus a stable client key, so React keeps input focus/cursor when rows re-sort. */
export type EditorRow = Row & { _key: string };

let keySeq = 0;

/** Tag API rows with stable client keys for editing. */
export function withKeys(rows: Row[]): EditorRow[] {
  return rows.map((row) => ({ ...row, _key: `r${keySeq++}` }));
}

/** A fresh key for a client-created row (insert/split). */
export function newKey(): string {
  return `r${keySeq++}`;
}

/** Drop the client-only key before sending rows to the server. */
export function stripKeys(rows: EditorRow[]): Row[] {
  return rows.map((row) => {
    const copy: Partial<EditorRow> = { ...row };
    delete copy._key;
    return copy as Row;
  });
}

/** Editor rows sorted by page range (the table always renders in ascending order). */
export function sortRows<T extends Row>(rows: T[]): T[] {
  return [...rows].sort((a, b) => a.start - b.start || a.end - b.end);
}

/**
 * Client-side row validation, mirroring the server rules (app/services/rows.py). Gaps between
 * documents are allowed on purpose (users skip junk pages); overlaps are not. Returns a map of
 * row index -> first error message.
 */
export function rowErrors(rows: Row[], totalPages: number): Map<number, string> {
  const errors = new Map<number, string>();
  let previousEnd = 0;
  rows.forEach((row, i) => {
    const s = Number(row.start);
    const e = Number(row.end);
    if (!Number.isInteger(s) || !Number.isInteger(e)) {
      errors.set(i, "pages must be numbers");
    } else if (s < 1 || e > totalPages || s > e) {
      errors.set(i, `needs 1 <= start <= end <= ${totalPages}`);
    } else if (s <= previousEnd) {
      errors.set(i, "overlaps the previous document");
    }
    previousEnd = Math.max(previousEnd, Number.isInteger(e) ? e : previousEnd);
  });
  return errors;
}
