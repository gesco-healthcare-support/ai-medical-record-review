import { apiFetch } from "@/lib/api";
import type { DocumentListItem } from "@/lib/types";

/** GET /api/documents - the landing list (owner-scoped, newest first, with active_job + rows_count).
 *  `owner` asks for another reviewer's records; the server honours it for an admin only and
 *  returns the caller's own list to anyone else. */
export function listDocuments(owner?: number | null) {
  const query = owner == null ? "" : `?owner=${encodeURIComponent(String(owner))}`;
  return apiFetch<DocumentListItem[]>(`/documents${query}`);
}

/** Where an upload goes. `owner` (admins only) is another reviewer's account; `allowDuplicate`
 *  confirms it after the server said that reviewer already holds the same file. */
export type UploadTarget = { owner?: number | null; allowDuplicate?: boolean };

/** POST /api/documents - single-PDF upload (multipart field "pdf"). Does NOT start identification.
 *  Into another reviewer's account a file they already hold is a 409 with code "duplicate" until
 *  `allowDuplicate` is sent; into one's own it is only `sha256_duplicate` in the answer. */
export function uploadDocument(file: File, target: UploadTarget = {}) {
  const form = new FormData();
  form.append("pdf", file);
  if (target.owner != null) form.append("owner", String(target.owner));
  if (target.allowDuplicate) form.append("allow_duplicate", "true");
  return apiFetch<{ id: string; page_count: number; sha256_duplicate: boolean }>("/documents", {
    method: "POST",
    body: form,
  });
}

/** POST /api/documents/aggregate - combine several pre-split PDFs into one record (field "pdfs"),
 *  with an optional display name for the record. `owner` (admins only) puts the record in another
 *  reviewer's account. */
export function aggregateDocuments(name: string, files: File[], owner?: number | null) {
  const form = new FormData();
  if (name.trim()) form.append("name", name.trim());
  if (owner != null) form.append("owner", String(owner));
  for (const file of files) form.append("pdfs", file);
  return apiFetch<{ id: string; page_count: number; records: unknown[] }>("/documents/aggregate", {
    method: "POST",
    body: form,
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
