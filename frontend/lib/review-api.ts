import { apiFetch } from "@/lib/api";
import type {
  DocumentDetail,
  DocumentStatus,
  JobProgress,
  Row,
  SummaryItem,
} from "@/lib/types";

/** GET /api/documents/{id} - full editor payload (listing + rows + category options). */
export function getDocument(id: string) {
  return apiFetch<DocumentDetail>(`/documents/${id}`);
}

/** GET /api/documents/{id}/status - polled every 1s while a job runs. */
export function getStatus(id: string) {
  return apiFetch<{ status: DocumentStatus; job: JobProgress | null }>(
    `/documents/${id}/status`,
  );
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

/** POST /api/documents/{id}/summarize/start - flush rows + enqueue summarization. */
export function startSummarize(id: string, rows: Row[]) {
  return apiFetch<{ ok: boolean }>(`/documents/${id}/summarize/start`, {
    method: "POST",
    body: JSON.stringify({ rows }),
  });
}

/** GET /api/documents/{id}/summaries - the drafted summaries (all; paginated client-side). */
export function getSummaries(id: string) {
  return apiFetch<SummaryItem[]>(`/documents/${id}/summaries`);
}

/** PUT /api/documents/{id}/summaries/{idx} - reviewer edits (title/date/text) or exclude toggle. */
export function putSummary(
  id: string,
  idx: number,
  patch: Partial<{
    summaryTitle: string;
    summaryDate: string;
    summaryText: string;
    excluded: boolean;
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
