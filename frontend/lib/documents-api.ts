import { apiFetch } from "@/lib/api";
import type { ReceivedKind } from "@/lib/received-files";
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

/** What a record is put together from: the patient's name and ID, the day the records were
 *  received, and the files in the order to join them, each with its kind. `owner` (admins only)
 *  puts the record in another reviewer's account. */
export type ReceivedFilesUpload = {
  lastName: string;
  firstName: string;
  patientId: string;
  /** `YYYY-MM-DD`, as the browser's date box gives it. */
  dateReceived: string;
  files: { file: File; kind: ReceivedKind }[];
  owner?: number | null;
};

/** POST /api/documents/combine - one record from the files it arrived in, joined in the order sent
 *  (repeated fields "pdfs" and "kinds", one kind per file). Does NOT start identification. */
export function combineReceivedFiles(upload: ReceivedFilesUpload) {
  const form = new FormData();
  form.append("last_name", upload.lastName.trim());
  form.append("first_name", upload.firstName.trim());
  form.append("patient_id", upload.patientId);
  form.append("date_received", upload.dateReceived);
  if (upload.owner != null) form.append("owner", String(upload.owner));
  for (const { file, kind } of upload.files) {
    form.append("pdfs", file);
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

export function downloadRawPdf(id: string) {
  // A detached anchor, as for a prepared download: same-origin, so the session cookie goes with it.
  const link = document.createElement("a");
  link.href = rawPdfUrl(id);
  link.download = "";
  link.click();
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
