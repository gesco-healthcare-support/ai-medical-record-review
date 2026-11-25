/** The office's billing sheet ("super bill") for a record put together from received files. The
 *  choices mirror `backend/app/services/billing_sheet.py` (`VISIT_TYPES`), which the server checks
 *  every value against. Who reviews the records is not asked: the office reviews every record in
 *  house, and the sheet always says IN-HOUSE (2026-11-20). */

/** The type of visit. A supplemental report (SUPP) has no appointment: the sheet's DOS says SUPP.
 *  Records that came in too late for the evaluation (late meds) have none either: stored as LATE
 *  (the server's column holds 8 characters), shown as "Late Meds", and the sheet's DOS and type of
 *  visit say LATE MEDS. */
export const VISIT_TYPES = ["AME", "QME", "PQME", "IME", "SUPP", "LATE"] as const;
export type VisitType = (typeof VISIT_TYPES)[number];
export const SUPPLEMENTAL: VisitType = "SUPP";
export const LATE_MEDS: VisitType = "LATE";

/** How a type of visit is shown in the dropdown. */
export function visitLabel(visit: string): string {
  return visit === LATE_MEDS ? "Late Meds" : visit;
}

/** Whether the type of visit has an appointment date: every one but a supplemental report and
 *  late meds, whose report is due 40 days after the records were received. */
export function takesAppointment(visit: string): boolean {
  return visit !== SUPPLEMENTAL && visit !== LATE_MEDS;
}

/** The longest typed text the sheet keeps (the claim numbers, the location, the reason the counts
 *  differ). */
export const BILLING_TEXT_MAX = 255;

/** A typed page count: blank, or a whole number. */
export function isPageCount(value: string): boolean {
  return value.trim() === "" || /^\d+$/.test(value.trim());
}

/** The record's billing sheet, as GET/PUT /api/documents/{id}/billing answer it: what was typed
 *  (empty for unset), the counts worked out from the record (`counted`: the office count, and the
 *  deposition pages - null while `pages_unread` of its `pages_counted` pages wait to be read; an
 *  older server sent `counted: null` until every page was read), the counts typed over them, and
 *  the doctors. */
export type BillingSheet = {
  visit_type: string;
  appointment_date: string;
  doctor: string;
  interpreter: "" | "yes" | "no";
  claim_numbers: string;
  location: string;
  previous_visit: string;
  declared_pages: number | "";
  discrepancy_reason: string;
  records_from: string;
  received_on: string;
  office_count: number | "";
  depo_pages: number | "";
  counted: { office: number; depo: number | null } | null;
  pages_counted: number;
  pages_unread: number;
  doctors: string[];
};

/** What PUT /api/documents/{id}/billing takes: every field as typed; an empty one is cleared, and
 *  an empty count goes back to the worked-out one. */
export type BillingSheetEdit = {
  visit_type: string;
  appointment_date: string;
  doctor: string;
  interpreter: string;
  claim_numbers: string;
  location: string;
  previous_visit: string;
  declared_pages: string;
  discrepancy_reason: string;
  records_from: string;
  office_count: string;
  depo_pages: string;
};

/** The billing fields typed at upload (and shown again on the sheet's dialog), as the form holds
 *  them: dates `YYYY-MM-DD` (the browser's date box), the declared page count as typed, `""` for
 *  not given. */
export type BillingDraft = Pick<
  BillingSheetEdit,
  | "visit_type"
  | "appointment_date"
  | "doctor"
  | "interpreter"
  | "claim_numbers"
  | "location"
  | "previous_visit"
  | "declared_pages"
>;

export const EMPTY_BILLING: BillingDraft = {
  visit_type: "",
  appointment_date: "",
  doctor: "",
  interpreter: "",
  claim_numbers: "",
  location: "",
  previous_visit: "",
  declared_pages: "",
};

/** Whether the billing fields typed at upload are enough to make the record: the type of visit,
 *  who reviews the records and the interpreter are required, and the appointment date unless the
 *  visit has none (a supplemental report, late meds); a declared page count, when given, is a whole number, and the
 *  claim numbers and the location fit the sheet. */
export function billingReady(draft: BillingDraft): boolean {
  return (
    draft.visit_type !== "" &&
    (!takesAppointment(draft.visit_type) || draft.appointment_date !== "") &&
    draft.interpreter !== "" &&
    isPageCount(draft.declared_pages) &&
    draft.claim_numbers.length <= BILLING_TEXT_MAX &&
    draft.location.length <= BILLING_TEXT_MAX
  );
}

/** The editable part of a sheet the server sent, as the form holds it. */
export function toEdit(sheet: BillingSheet): BillingSheetEdit {
  return {
    visit_type: sheet.visit_type,
    appointment_date: sheet.appointment_date,
    doctor: sheet.doctor,
    interpreter: sheet.interpreter,
    claim_numbers: sheet.claim_numbers,
    location: sheet.location,
    previous_visit: sheet.previous_visit,
    declared_pages: String(sheet.declared_pages),
    discrepancy_reason: sheet.discrepancy_reason,
    records_from: sheet.records_from,
    office_count: String(sheet.office_count),
    depo_pages: String(sheet.depo_pages),
  };
}

/** Whether an edit can be saved: counts are whole numbers, texts within the sheet's limit. */
export function editValid(edit: BillingSheetEdit): boolean {
  return (
    isPageCount(edit.declared_pages) &&
    isPageCount(edit.office_count) &&
    isPageCount(edit.depo_pages) &&
    edit.claim_numbers.length <= BILLING_TEXT_MAX &&
    edit.location.length <= BILLING_TEXT_MAX &&
    edit.discrepancy_reason.length <= BILLING_TEXT_MAX
  );
}

/** The counts the sheet will print: a typed count, else the worked-out one, else none - printed
 *  blank (the deposition pages while the record's pages are still being read). */
export function shownCounts(sheet: BillingSheet): { office: number | null; depo: number | null } {
  const office = sheet.office_count === "" ? (sheet.counted?.office ?? null) : sheet.office_count;
  const depo = sheet.depo_pages === "" ? (sheet.counted?.depo ?? null) : sheet.depo_pages;
  return { office, depo };
}

/** Whether the sheet as printed now would leave the deposition pages blank: neither typed nor
 *  worked out yet. The sheet still downloads; this only says so. */
export function depoPending(sheet: BillingSheet): boolean {
  return shownCounts(sheet).depo === null;
}
