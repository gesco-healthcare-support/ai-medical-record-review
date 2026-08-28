/** Shape returned by GET /api/users/me (FastAPI-Users UserRead + our required display name). */
export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
};

// "dedup" belongs here: GET /documents/{id}/duplicates returns a dedup Job.progress(), so leaving it
// out made every read of that job's kind unsound.
export type JobKind = "segment" | "classify" | "summarize" | "dedup";
// paused/needs_attention are resumable-summarize states (item 7): paused = auto-resuming after a
// transient failure; needs_attention = a permanent failure the reviewer must resolve.
// `cancelled` is deliberately distinct from `error` and `interrupted`: error is a pipeline fault,
// interrupted is the system losing the job, cancelled is the reviewer asking for it. Lumping them
// together would make the failure numbers lie about how often the pipeline actually breaks.
export type JobState =
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "needs_attention"
  | "error"
  | "interrupted"
  | "cancelled";

/** One sub-document that permanently failed to summarize (non-PHI: position + page range + reason). */
export type FailedRow = {
  idx: number;
  pages: string;
  reason: string;
};

/** Detail a needs_attention summarize run carries: the friendly message + the rows that failed. */
export type JobAttention = {
  message: string;
  rows: FailedRow[];
};

/** Job.progress() from the backend, embedded per document in the listing. */
export type JobProgress = {
  // Needed to cancel THIS job: the endpoint is scoped by job id rather than by document, so pressing
  // Stop cannot kill a different job that started between the render and the click.
  id: number;
  kind: JobKind;
  state: JobState;
  stage: string;
  current: number;
  total: number;
  error: string | null;
  attention?: JobAttention | null;
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
  /** Server-computed, read-only: a high-precision rule named this TITLE as administrative
   *  paperwork. Combined with the LIVE `category` by `couldNotIdentify`, so re-classifying a row
   *  updates the filter without waiting for a save. Optional because rows the editor creates
   *  itself (insert, split, merge) never carry it, and none of them is ruled paperwork. */
  ruled_paperwork?: boolean;
  /** Server-computed, read-only: which cascade path decided this row's category, frozen at segment
   *  time - `rules`, `llm+embedding`, `llm-disagree`, `embedding-only`, `llm-only`, `no-signal`,
   *  `empty`, or `timeout`. Absent or null means UNKNOWN (the row predates the column, or the
   *  editor created it) and must never be read as "confidently classified". */
  method?: string | null;
};

/** A selectable category ({id, name}) from catalog.get_category_options. */
export type CategoryOption = { id: string; name: string };

/** A drafted summary (Summary.listing()). */
export type VerifyIssue = { type: string; detail: string };

export type SummaryItem = {
  idx: number;
  summaryTitle: string;
  summaryDate: string;
  summaryText: string;
  manualCheck: boolean;
  excluded: boolean;
  edited: boolean;
  // Faithfulness verify pass: `verified` = the pass ran; `verifyChanged` = the AI corrected this
  // summary (issues were found). `verifyIssues` carries the {type, detail} list for later UIs.
  verified: boolean;
  verifyChanged: boolean;
  verifyIssues: VerifyIssue[];
  // `row.category` is the category that GENERATED this text. `rowCategoryLive` is what the row says
  // NOW: they differ when the reviewer re-classified the sub-document but has not re-drafted yet, and
  // that difference is what the "Category changed" badge reads. Null when no row covers this summary's
  // page range any more (boundaries were re-segmented) - absence of a live value, not a mismatch.
  row: { start: number; end: number; category: string };
  rowCategoryLive: string | null;
};

/** One copy within a duplicate cluster (Duplicates tab). */
export type DuplicateRow = {
  idx: number;
  title: string;
  date: string;
  pages: { start: number; end: number };
  include: boolean;
  primary: boolean;
};

/** A confirmed cluster of sub-documents the dedup pass believes are the same document. */
export type DuplicateCluster = {
  group: number;
  dismissed: boolean;
  /** How alike the members' text is, 0-1: ~1.0 means re-scans of one document, a low value means a
   *  recurring form series that merely shares a template. Null for clusters stored before the score
   *  was kept. */
  similarity: number | null;
  rows: DuplicateRow[];
};

/** GET /documents/{id}/duplicates - the clusters plus the latest dedup job's progress. */
export type DuplicatesResponse = {
  clusters: DuplicateCluster[];
  job: JobProgress | null;
  /** Boundaries changed since the last duplicate check, so the clusters may be incomplete. Drives
   *  the manual "re-check duplicates" hint - the app never re-runs clustering on its own. */
  stale: boolean;
  /** Sub-documents the last completed check could not read (no OCR text). Empty text matches
   *  nothing, so these were never compared and any duplicate involving them was missed - the tab has
   *  to say so rather than present the run as a clean result. */
  unreadable: number;
  /** Whether a duplicate check has ever COMPLETED on this document. Empty `clusters` alone cannot say:
   *  a completed run that found nothing and a document never checked at all both produce `[]`, and
   *  dedup is gated behind the review phase so "never checked" is the common case. Without this the
   *  tab reports "No duplicate documents found" on a record nothing has looked at. */
  checked: boolean;
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
