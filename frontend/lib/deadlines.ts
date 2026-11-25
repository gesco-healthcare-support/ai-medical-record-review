/** When a record's report is due, and how My documents shows it (the office's answer, 2026-11-25).
 *
 *  The server works the date out (`due_on` on each listed record, `Document.due_on` in
 *  `backend/app/models.py`): a visit's appointment date, or - for a supplemental report and late
 *  meds, which have no appointment - 40 days after the records were received. Records are listed
 *  closest deadline first; a record with no deadline (one uploaded as a single PDF) comes last.
 *
 *  The row's colour says what kind of deadline it is: yellow for an appointment, light blue for
 *  late meds, the normal colour for a supplemental report. A record marked Completed has no
 *  colour: its work is done. */
import { LATE_MEDS, SUPPLEMENTAL, visitLabel } from "@/lib/billing-sheet";
import type { DocumentListItem } from "@/lib/types";

export type DeadlineTone = "appointment" | "late-meds" | null;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Days from `today` to the `YYYY-MM-DD` date `due`: 0 on the day, negative once it has passed. */
export function daysLeft(due: string, today: Date): number {
  const [year, month, day] = due.split("-").map(Number);
  const dueDay = Date.UTC(year, month - 1, day);
  const thisDay = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((dueDay - thisDay) / DAY_MS);
}

/** "5 days left", "1 day left", "Due today", "3 days late". */
export function daysLeftLabel(days: number): string {
  if (days === 0) return "Due today";
  const count = Math.abs(days);
  const unit = count === 1 ? "day" : "days";
  return days > 0 ? `${count} ${unit} left` : `${count} ${unit} late`;
}

/** `MM/DD/YYYY`, the way the office writes dates. */
export function usDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${month}/${day}/${year}`;
}

/** The colour of the record's row on My documents. */
export function deadlineTone(doc: DocumentListItem): DeadlineTone {
  if (!doc.due_on || doc.completed_at) return null;
  if (doc.visit_type === LATE_MEDS) return "late-meds";
  if (doc.visit_type === SUPPLEMENTAL) return null;
  return "appointment";
}

/** The second line under the due date: the kind of deadline when it is not an appointment, and
 *  the days left. */
export function deadlineNote(doc: DocumentListItem, today: Date): string {
  if (!doc.due_on) return "";
  const left = daysLeftLabel(daysLeft(doc.due_on, today));
  const visit = doc.visit_type ?? "";
  return visit === SUPPLEMENTAL || visit === LATE_MEDS ? `${visitLabel(visit)} - ${left}` : left;
}

/** The order of the Due column: records not yet Completed first, then by deadline, closest first;
 *  a record with no deadline after every dated one. */
export function deadlineSortKey(doc: DocumentListItem): string {
  return `${doc.completed_at ? 1 : 0}|${doc.due_on || "9999-12-31"}`;
}
