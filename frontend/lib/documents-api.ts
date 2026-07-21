import { apiFetch } from "@/lib/api";
import type { DocumentListItem } from "@/lib/types";

/** GET /api/documents - the landing list (owner-scoped, newest first, with active_job + rows_count). */
export function listDocuments() {
  return apiFetch<DocumentListItem[]>("/documents");
}

/** POST /api/documents - single-PDF upload (multipart field "pdf"). Does NOT start identification. */
export function uploadDocument(file: File) {
  const form = new FormData();
  form.append("pdf", file);
  return apiFetch<{ id: string; page_count: number; sha256_duplicate: boolean }>("/documents", {
    method: "POST",
    body: form,
  });
}

/** POST /api/documents/aggregate - combine several pre-split PDFs into one record (field "pdfs"),
 *  with an optional display name for the record. */
export function aggregateDocuments(name: string, files: File[]) {
  const form = new FormData();
  if (name.trim()) form.append("name", name.trim());
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
