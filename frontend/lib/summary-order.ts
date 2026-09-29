/** Finding a summary on the Summaries tab: search and date order.
 *
 *  The client's lead reviewer: "when trying to go back and fix something I want an easier way to be
 *  able to find specific summaries" - either a search, or the entries "chronologically rather than by
 *  page number". Both are here, as pure functions so the tab only wires them up. */

import type { SummaryItem } from "@/lib/types";

export type SummaryOrder = "pages" | "date";

// MM/DD/YY or MM/DD/YYYY, separated by "/", "-" or ".": the shapes a summary date takes. Anything
// else - "-", "Undated", free text - is undated, never guessed at.
const DATE = /^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\s*$/;

/** A summary date as a sortable number, or null when it states none. A two-digit year follows the
 *  same rule as the exported document (Python's `%y`): 69-99 are the 1900s, 00-68 the 2000s. */
export function summaryDateValue(date: string): number | null {
  const m = DATE.exec(date || "");
  if (!m) return null;
  const month = Number(m[1]);
  const day = Number(m[2]);
  let year = Number(m[3]);
  if (m[3].length === 2) year += year >= 69 ? 1900 : 2000;
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return year * 10000 + month * 100 + day;
}

/** The summaries whose title, text or date contain every word of `query`, case-insensitive. An
 *  empty query keeps them all. Every word must match, so "smith mri" finds the one MRI by Smith
 *  rather than every summary mentioning either. */
export function findSummaries(items: SummaryItem[], query: string): SummaryItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return items;
  return items.filter((item) => {
    const haystack = `${item.summaryTitle} ${item.summaryText} ${item.summaryDate}`.toLowerCase();
    return words.every((w) => haystack.includes(w));
  });
}

/** `items` in page order (as the server sends them) or oldest date first. Undated summaries go
 *  last, as they do in the exported document, and ties keep their page order. */
export function orderSummaries(items: SummaryItem[], order: SummaryOrder): SummaryItem[] {
  if (order === "pages") return items;
  return items
    .map((item, position) => ({ item, position, when: summaryDateValue(item.summaryDate) }))
    .sort((a, b) => {
      if (a.when === b.when) return a.position - b.position;
      if (a.when === null) return 1;
      if (b.when === null) return -1;
      return a.when - b.when;
    })
    .map((entry) => entry.item);
}
