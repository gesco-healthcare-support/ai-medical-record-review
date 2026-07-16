/** Shape returned by GET /api/users/me (FastAPI-Users UserRead + our required display name). */
export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
};

export type JobKind = "segment" | "classify" | "summarize";
export type JobState = "queued" | "running" | "done" | "error" | "interrupted";

/** Job.progress() from the backend, embedded per document in the listing. */
export type JobProgress = {
  kind: JobKind;
  state: JobState;
  stage: string;
  current: number;
  total: number;
  error: string | null;
};

/** Document.status lifecycle (see app/services/jobs.py + worker/tasks.py). */
export type DocumentStatus =
  | "uploaded"
  | "segmenting"
  | "summarizing"
  | "reviewing"
  | "done"
  | "error"
  | "interrupted";

/** One row of GET /api/documents (Document.listing() + rows_count). */
export type DocumentListItem = {
  id: string;
  original_filename: string;
  page_count: number;
  status: DocumentStatus;
  created_at: string;
  updated_at: string;
  active_job: JobProgress | null;
  rows_count: number;
};
