import { apiFetch } from "@/lib/api";
import {
  type BillingDraft,
  type BillingSheet,
  type BillingSheetEdit,
  SUPPLEMENTAL,
} from "@/lib/billing-sheet";
import type { ReceivedKind, RecordsFrom } from "@/lib/received-files";
import type { DocumentListItem } from "@/lib/types";

/** GET /api/documents - the landing list (owner-scoped, newest first, with active_job + rows_count).
 *  `owner` asks for another reviewer's records; the server honours it for an admin only and
 *  returns the caller's own list to anyone else. */
export function listDocuments(owner?: number | null) {
  const query = owner == null ? "" : `?owner=${encodeURIComponent(String(owner))}`;
  return apiFetch<DocumentListItem[]>(`/documents${query}`);
}

/** Where an upload goes. `owner` (admins only) is another reviewer's account; `allowDuplicate`
 *  confirms it after the server said that reviewer already holds the same file. `patientId` is the
 *  patient's ID - read from the file name, or typed when the name has none. */
export type UploadTarget = {
  owner?: number | null;
  allowDuplicate?: boolean;
  patientId?: string | null;
};

/** What an upload answers with about the patient: the stored ID ("" for none) and how many OTHER
 *  records, in any reviewer's account, carry it. */
export type PatientFields = { patient_id: string; patient_records: number };

/** POST /api/documents - single-PDF upload (multipart field "pdf"). Does NOT start identification.
 *  Into another reviewer's account a file they already hold is a 409 with code "duplicate" until
 *  `allowDuplicate` is sent; into one's own it is only `sha256_duplicate` in the answer. */
export function uploadDocument(file: File, target: UploadTarget = {}) {
  const form = new FormData();
  form.append("pdf", file);
  if (target.owner != null) form.append("owner", String(target.owner));
  if (target.allowDuplicate) form.append("allow_duplicate", "true");
  if (target.patientId) form.append("patient_id", target.patientId);
  return apiFetch<{ id: string; page_count: number; sha256_duplicate: boolean } & PatientFields>(
    "/documents",
    { method: "POST", body: form },
  );
}

/** One of the files a record arrived in, kept on the server until the record is put together:
 *  its token, its page count, the kind its first page says it is, whether the uploader should look
 *  (`check`: the model was unsure, or off), and its fingerprint as received. */
export type StagedFile = {
  token: string;
  pages: number;
  kind: ReceivedKind;
  check: boolean;
  sha256: string;
};

/** POST /api/documents/combine/files - send one received file as soon as it is added. The server
 *  keeps it, counts its pages and reads its first page to say what it is. One file per request, so
 *  each file is held to the proxy's upload limit on its own. */
export function stageReceivedFile(file: File) {
  const form = new FormData();
  form.append("pdf", file);
  return apiFetch<StagedFile>("/documents/combine/files", { method: "POST", body: form });
}

/** GET /api/documents/combine/files/{token}/preview - a small PNG of a staged file's first page,
 *  shown by a plain image tag (same origin, so the session cookie goes with it). */
export function receivedFilePreviewUrl(token: string) {
  return `/api/documents/combine/files/${encodeURIComponent(token)}/preview`;
}

/** DELETE /api/documents/combine/files/{token} - drop a staged file (removed from the list, or the
 *  dialog closed). Harmless when it is already gone. */
export function unstageReceivedFile(token: string) {
  return apiFetch<{ ok: boolean }>(`/documents/combine/files/${encodeURIComponent(token)}`, {
    method: "DELETE",
  });
}

/** What a record is put together from: the patient's name and ID, the day the records were
 *  received, who sent the records, the billing sheet's fields (`lib/billing-sheet.ts`), and the
 *  staged files in the order to join them, each with its kind. `owner` (admins only) puts the
 *  record in another reviewer's account. */
export type ReceivedFilesUpload = {
  lastName: string;
  firstName: string;
  patientId: string;
  /** `YYYY-MM-DD`, as the browser's date box gives it. */
  dateReceived: string;
  recordsFrom: RecordsFrom;
  billing: BillingDraft;
  files: { token: string; kind: ReceivedKind }[];
  owner?: number | null;
};

/** The billing fields as the server takes them. A supplemental report sends no appointment date;
 *  an optional field left empty is not sent. */
function appendBilling(form: FormData, billing: BillingDraft) {
  form.append("visit_type", billing.visit_type);
  if (billing.visit_type !== SUPPLEMENTAL) form.append("appointment_date", billing.appointment_date);
  form.append("review_source", billing.review_source);
  form.append("interpreter", billing.interpreter);
  const optional: [string, string][] = [
    ["doctor", billing.doctor],
    ["claim_numbers", billing.claim_numbers.trim()],
    ["location", billing.location.trim()],
    ["previous_visit", billing.previous_visit],
    ["declared_pages", billing.declared_pages.trim()],
  ];
  for (const [name, value] of optional) {
    if (value) form.append(name, value);
  }
}

/** POST /api/documents/combine - one record from the staged files, joined in the order sent
 *  (repeated fields "tokens" and "kinds", one kind per file). Does NOT start identification. */
export function combineReceivedFiles(upload: ReceivedFilesUpload) {
  const form = new FormData();
  form.append("last_name", upload.lastName.trim());
  form.append("first_name", upload.firstName.trim());
  form.append("patient_id", upload.patientId);
  form.append("date_received", upload.dateReceived);
  form.append("records_from", upload.recordsFrom);
  appendBilling(form, upload.billing);
  if (upload.owner != null) form.append("owner", String(upload.owner));
  for (const { token, kind } of upload.files) {
    form.append("tokens", token);
    form.append("kinds", kind);
  }
  return apiFetch<
    {
      id: string;
      page_count: number;
      parts: { kind: ReceivedKind; start: number; end: number }[];
    } & PatientFields
  >("/documents/combine", { method: "POST", body: form });
}

/** GET /api/documents/{id}/raw - the record's PDF as uploaded (for a record put together from
 *  several files, the joined file), saved under the record's own file name. Handed to the browser's
 *  download manager as an ordinary link, which streams it to disk (see lib/download.ts, #389). */
export function rawPdfUrl(id: string) {
  return `/api/documents/${encodeURIComponent(id)}/raw`;
}

/** Hand a same-origin download URL to the browser's download manager: a detached anchor, as for a
 *  prepared download, so the session cookie goes with it. */
function saveFrom(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.click();
}

export function downloadRawPdf(id: string) {
  saveFrom(rawPdfUrl(id));
}

/** GET /api/documents/combine/doctors - the doctors the upload dialog offers (the header's list). */
export function listDoctors() {
  return apiFetch<{ doctors: string[] }>("/documents/combine/doctors");
}

/** GET /api/documents/{id}/billing - the record's billing sheet, for the billing sheet dialog. */
export function getBillingSheet(id: string) {
  return apiFetch<BillingSheet>(`/documents/${encodeURIComponent(id)}/billing`);
}

/** PUT /api/documents/{id}/billing - edit the billing sheet. Answers as GET. */
export function saveBillingSheet(id: string, edit: BillingSheetEdit) {
  return apiFetch<BillingSheet>(`/documents/${encodeURIComponent(id)}/billing`, {
    method: "PUT",
    body: JSON.stringify(edit),
  });
}

/** GET /api/documents/{id}/billing-sheet - the billing sheet as a one-page PDF, named after the
 *  record. The dialog offers it only once both counts are known (the server answers 409 before). */
export function billingSheetUrl(id: string) {
  return `/api/documents/${encodeURIComponent(id)}/billing-sheet`;
}

export function downloadBillingSheet(id: string) {
  saveFrom(billingSheetUrl(id));
}

/** PUT /api/documents/{id}/patient-id - set the record's patient ID by hand ("" clears it).
 *  Answers with the record's list entry plus how many other records carry the ID. */
export function setPatientId(id: string, patientId: string) {
  return apiFetch<DocumentListItem & PatientFields>(`/documents/${id}/patient-id`, {
    method: "PUT",
    body: JSON.stringify({ patient_id: patientId }),
  });
}

/** PUT /api/documents/{id}/completed - the reviewer's Completed mark on the records list: set it
 *  or clear it. Answers with the record's list entry. */
export function setCompleted(id: string, completed: boolean) {
  return apiFetch<DocumentListItem>(`/documents/${id}/completed`, {
    method: "PUT",
    body: JSON.stringify({ completed }),
  });
}

/** DELETE /api/documents/{id} - cascades rows/summaries; 409 if a job is running. */
export function deleteDocument(id: string) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}`, { method: "DELETE" });
}

/** POST /api/documents/{id}/segment/start - enqueue identification; 409 if a job is already running. */
export function startIdentification(id: string) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/segment/start`, { method: "POST" });
}
