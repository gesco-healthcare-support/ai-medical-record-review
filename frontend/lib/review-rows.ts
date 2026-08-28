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
 *
 * `touched` closes the remaining hole: both of those fields are editable in the workbench too, so
 * taking the server's value unconditionally still reverted an unsaved re-classify or untick. Any
 * `_key:field` in the set is one the reviewer has changed since the last successful save, and their
 * value wins. Everything untouched still tracks the server, which is the whole point of the reload.
 */
export function applyServerRowChanges<T extends EditorRow>(
  local: T[],
  server: Row[],
  touched: ReadonlySet<string> = new Set(),
): T[] {
  const bySpan = new Map(server.map((row) => [`${row.start}-${row.end}`, row]));
  return local.map((row) => {
    const match = bySpan.get(`${row.start}-${row.end}`);
    if (!match) return row;
    return {
      ...row,
      include: touched.has(touchKey(row, "include")) ? row.include : match.include,
      category: touched.has(touchKey(row, "category")) ? row.category : match.category,
    };
  });
}

/** The two fields another tab can write, and so the only two that can collide with a local edit. */
export const SERVER_WRITABLE_FIELDS = ["include", "category"] as const;

/** Identity for one field of one row in the touched set: the client key, which survives an edit to
 *  any of the row's values (only a split or an insert mints a new one). */
export function touchKey(row: { _key: string }, field: string): string {
  return `${row._key}:${field}`;
}

/**
 * The `_key:field` entries to add to the touched set for an edit that turns `previous` into `next`.
 *
 * Only the server-writable fields are tracked, because they are the only ones
 * `applyServerRowChanges` would otherwise overwrite. Tracking every field would grow a set nothing
 * reads.
 */
export function touchedFields(previous: EditorRow[], next: EditorRow[]): string[] {
  const before = new Map(previous.map((row) => [row._key, row]));
  const keys: string[] = [];
  for (const row of next) {
    const was = before.get(row._key);
    if (!was) continue; // a brand-new row carries the reviewer's values in full already
    for (const field of SERVER_WRITABLE_FIELDS) {
      if (was[field] !== row[field]) keys.push(touchKey(row, field));
    }
  }
  return keys;
}

/** The General category, which is also where the cascade parks anything it could not answer. */
const GENERAL = "100";

/** The one `method` that means General was a CONFIDENT answer: the embedding stage and the LLM
 *  independently both said paperwork. Every other value is a guess, a single-signal answer, or a
 *  failure - all of which the reviewer asked to see. */
const CONFIDENT_PAPERWORK = "llm+embedding";

/**
 * A sub-document nothing identified: it sits in General and no rule put it there.
 *
 * The reviewers asked to keep these out of the summary but still look through them in case
 * something important is in there (issue #144), and today they cannot be listed - General holds
 * both "this is paperwork" and "we could not tell", and `flag` does not separate them either
 * (74% of rows carry it, and on a rule-matched row that can only be the segmenter).
 *
 * A row whose title a rule sends somewhere OTHER than General counts as one of these. It is
 * stored at 100 only because it was segmented before that rule shipped, so it needs a look rather
 * than being settled paperwork - which is why this tests `ruled_paperwork` and not "no rule".
 *
 * `ruled_paperwork` and `method` answer DIFFERENT questions and both are needed. The first is a
 * live replay of today's rules; the second is frozen at segment time and is the only record of how
 * confident the cascade was when it ran. So a row can be `method: "rules"` yet not ruled paperwork
 * today - the rule was since narrowed, nobody currently calls it paperwork, and it belongs in the
 * list. Only `llm+embedding` is excluded on the method, because only there did two independent
 * signals agree. A missing method means UNKNOWN, never confident: every row segmented before the
 * column existed reads that way, and they stay in the list exactly as they were.
 */
export function couldNotIdentify(row: Row): boolean {
  if (String(row.category) !== GENERAL) return false;
  return !row.ruled_paperwork && row.method !== CONFIDENT_PAPERWORK;
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
