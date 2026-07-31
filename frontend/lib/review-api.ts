import { apiFetch } from "@/lib/api";
import type {
  DocumentDetail,
  DocumentStatus,
  DuplicatesResponse,
  JobProgress,
  Row,
  SummaryItem,
} from "@/lib/types";

/** GET /api/documents/{id} - full editor payload (listing + rows + category options). */
export function getDocument(id: string) {
  return apiFetch<DocumentDetail>(`/documents/${id}`);
}

/** GET /api/documents/{id}/status - polled every 1s while a job runs. `unreviewed_duplicate_groups`
 *  is advisory only (drives the Duplicates badge/notice); it never blocks Summarize. */
export function getStatus(id: string) {
  return apiFetch<{
    status: DocumentStatus;
    job: JobProgress | null;
    unreviewed_duplicate_groups?: number;
  }>(`/documents/${id}/status`);
}

/** GET /api/documents/{id}/duplicates - confirmed duplicate clusters + the latest dedup progress. */
export function getDuplicates(id: string) {
  return apiFetch<DuplicatesResponse>(`/documents/${id}/duplicates`);
}

/** POST /api/documents/{id}/dedup/start - (re)run duplicate clustering (409 if a job is active). */
export function startDedup(id: string) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/dedup/start`, { method: "POST" });
}

/** One resolution of a duplicate cluster: keep a copy, dismiss the cluster, or drop one member out
 *  of it (the mixed cluster where some copies are real duplicates and others are not). */
export type DuplicateAction = "keep_one" | "dismiss" | "remove_member";

/** POST /api/documents/{id}/duplicates/{group}/resolve - keep-one (primaryIdx), dismiss, or
 *  remove_member (idx). */
export function resolveDuplicate(
  id: string,
  group: number,
  action: DuplicateAction,
  primaryIdx?: number,
  idx?: number,
) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/duplicates/${group}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action, primary_idx: primaryIdx ?? null, idx: idx ?? null }),
  });
}

/** PUT /api/documents/{id}/rows - autosave the editor rows (only sent for valid states). */
export function saveRows(id: string, rows: Row[]) {
  return apiFetch<{ ok: boolean; count: number }>(`/documents/${id}/rows`, {
    method: "PUT",
    body: JSON.stringify({ rows }),
  });
}

/** POST /api/documents/{id}/segment/start - enqueue identification (409 if a job runs). */
export function startSegment(id: string) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/segment/start`, { method: "POST" });
}

/** POST /api/documents/{id}/summarize/start - flush rows + enqueue summarization. `fresh` clears
 *  prior summaries first ("Re-summarize all"); otherwise the resumable worker reuses done rows. */
export function startSummarize(id: string, rows: Row[], fresh = false) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/summarize/start`, {
    method: "POST",
    body: JSON.stringify({ rows, fresh }),
  });
}

/** The persisted, reviewer-editable report-header fields (patient name split into first/last). */
export type HeaderFields = {
  patient_first_name: string;
  patient_last_name: string;
  patient_dob: string;
  law_firm: string;
};

/** POST /api/documents/{id}/extract-header - re-extract the header from the record (Vertex). Does
 *  NOT persist; the caller populates the editable bar and the reviewer saves via saveHeader. */
export function extractHeader(id: string) {
  return apiFetch<HeaderFields>(`/documents/${id}/extract-header`, { method: "POST" });
}

/** PUT /api/documents/{id}/header - persist the reviewer-edited report header. */
export function saveHeader(id: string, fields: HeaderFields) {
  return apiFetch<unknown>(`/documents/${id}/header`, {
    method: "PUT",
    body: JSON.stringify(fields),
  });
}

/** GET /api/documents/{id}/summaries - the drafted summaries (all; paginated client-side). */
export function getSummaries(id: string) {
  return apiFetch<SummaryItem[]>(`/documents/${id}/summaries`);
}

/** PUT /api/documents/{id}/summaries/{idx} - reviewer edits (title/date/text), exclude toggle, or a
 *  re-classification. `category` is unlike the others: the server writes it to the owning ReviewRow,
 *  not to the summary, and refuses it (409) while ANY job is running - a segment job would replace the
 *  row set and swallow the edit. */
export function putSummary(
  id: string,
  idx: number,
  patch: Partial<{
    summaryTitle: string;
    summaryDate: string;
    summaryText: string;
    excluded: boolean;
    category: string;
  }>,
) {
  return apiFetch<SummaryItem>(`/documents/${id}/summaries/${idx}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

/** POST /api/documents/{id}/summaries/{idx}/resummarize - re-run one summary (discards edits). */
export function resummarize(id: string, idx: number) {
  return apiFetch<SummaryItem>(`/documents/${id}/summaries/${idx}/resummarize`, {
    method: "POST",
  });
}
