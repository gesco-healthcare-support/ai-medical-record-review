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
// paused/needs_attention are resumable-summarize states (item 7): paused = auto-resuming after a
// transient failure; needs_attention = a permanent failure the reviewer must resolve.
export type JobState =
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "needs_attention"
  | "error"
  | "interrupted";

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
  | "needs_attention"
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
  patient_first_name: string;
  patient_last_name: string;
  patient_name: string;
  patient_dob: string;
  law_firm: string;
};

/** A sub-document row in the review editor (ReviewRow.as_row()). */
export type Row = {
  start: number;
  end: number;
  category: string;
  title: string;
  date: string;
  injury_date: string;
  flag: string;
  suggest_merge: boolean;
  include: boolean;
};

/** A selectable category ({id, name}) from catalog.get_category_options. */
export type CategoryOption = { id: string; name: string };

/** A drafted summary (Summary.listing()). */
export type SummaryItem = {
  idx: number;
  summaryTitle: string;
  summaryDate: string;
  summaryText: string;
  manualCheck: boolean;
  excluded: boolean;
  edited: boolean;
  row: { start: number; end: number; category: string };
};

/** GET /api/documents/{id} - the full editor payload (listing + rows + category options). */
export type DocumentDetail = {
  id: string;
  original_filename: string;
  page_count: number;
  status: DocumentStatus;
  created_at: string;
  updated_at: string;
  active_job: JobProgress | null;
  patient_first_name: string;
  patient_last_name: string;
  patient_name: string;
  patient_dob: string;
  law_firm: string;
  rows: Row[];
  categories: CategoryOption[];
};
