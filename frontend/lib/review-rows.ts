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
 * Merge `lower` into `upper`, for "merge up" and "apply suggested merges".
 *
 * The merged row keeps the UPPER row's identity - title, date, category - which is deliberate:
 * merging means "this continues the document above". The reviewers were asked and said they would
 * rather the PR-2 gave the heading but will fix that by hand, so identity is not what this combines.
 *
 * `include` is NOT identity, it is the work item, and it is combined the same way `flag` is: if
 * either half was going to be summarized, the merged document is. Taking the upper row's value
 * verbatim silently dropped the lower one's content - a 52-page evaluation merged into a 2-page
 * cover sheet in category 100 (the one category that defaults to include:false) left the whole
 * evaluation inside an unchecked row, and the server writes the client's `include` through
 * unchanged. Nothing on screen names the document that stopped being summarized, and content that
 * reaches no deliverable is never surfaced again.
 */
export function mergeRows<T extends Row>(upper: T, lower: T): T {
  return {
    ...upper,
    end: Math.max(Number(upper.end), Number(lower.end)),
    flag: [upper.flag, lower.flag].includes("x") ? "x" : "-",
    include: upper.include !== false || lower.include !== false,
  };
}

/**
 * Apply the row changes another tab made server-side onto the editor's in-memory rows.
 *
 * Only the two fields another tab can write are taken from the server - `include` (Duplicates:
 * "keep this one") and `category` (Summaries: re-classify) - and only for a row the reviewer has
 * not re-spanned. Everything else stays local, because the local copy may hold edits that are not
 * saved yet and replacing the buffer wholesale is how they were being lost.
 *
 * Matching is by page span: rows carry no stable id, and neither of those two writes moves a
 * boundary. A local row whose span has no server twin keeps its own values - the reviewer has
 * redefined that document, so their split/merge is the newer fact.
 */
export function applyServerRowChanges<T extends Row>(local: T[], server: Row[]): T[] {
  const bySpan = new Map(server.map((row) => [`${row.start}-${row.end}`, row]));
  return local.map((row) => {
    const match = bySpan.get(`${row.start}-${row.end}`);
    return match ? { ...row, include: match.include, category: match.category } : row;
  });
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
