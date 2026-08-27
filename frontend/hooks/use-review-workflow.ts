"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { summariesKey } from "@/hooks/use-summaries";
import { humanizeError } from "@/lib/errors";
import {
  cancelJob,
  getDocument,
  getStatus,
  saveRows,
  startDedup,
  startSegment,
  startSummarize,
  type HeaderFields,
} from "@/lib/review-api";
import type { CategoryOption, DocumentStatus, FailedRow, JobKind } from "@/lib/types";
import {
  applyServerRowChanges,
  rowErrors,
  sortRows,
  stripKeys,
  touchedFields,
  withKeys,
  type EditorRow,
} from "@/lib/review-rows";
import type { StepId } from "@/components/review/stepper";

export type Section = "loading" | "start" | "progress" | "editor" | "summaries";

/** Autosave indicator state for the review header. */
export type SaveState = { kind: "" | "saved" | "dirty" | "error"; message?: string };

const STAGE_LABELS: Record<string, string> = {
  starting: "Starting...",
  reading: "Reading the pages",
  segmenting: "Finding document boundaries",
  categorizing: "Categorizing each document",
  verifying: "Double-checking uncertain boundaries",
  summarizing: "Writing summaries",
  paused: "Paused - waiting for capacity, will retry automatically",
};

/** How a polled job settled: finished cleanly, ended needing the reviewer's attention (with the
 *  sub-documents that failed, so the UI can name + highlight them), or was stopped by the reviewer.
 *
 *  `cancelled` has to be a SETTLED outcome, not merely a state the poller tolerates: any state this
 *  union does not name falls through to the keep-polling branch below, so a stopped job would spin the
 *  progress bar forever and a working stop button would look broken. */
type PollResult = {
  outcome: "done" | "needs_attention" | "cancelled";
  message?: string;
  rows?: FailedRow[];
};

function message(err: unknown, fallback: string) {
  return humanizeError(err, {
    fallback,
    notFound:
      "This record is no longer available to you - it may have been moved or deleted. Go back and refresh.",
  });
}

/** The identify -> review (-> summaries) lifecycle shared by /records/[id] and the category-bundle
 *  pages: boot from persisted state, poll a running job every 1s, autosave rows. A null documentId
 *  is idle (the bundle picker before a document is chosen). When enableSummaries is false (bundle),
 *  a finished record opens the editor instead of the summaries step, and summaries are never shown. */
export function useReviewWorkflow(
  documentId: string | null,
  options: { enableSummaries?: boolean } = {},
) {
  const enableSummaries = options.enableSummaries !== false;
  const queryClient = useQueryClient();

  const [section, setSection] = useState<Section>("loading");
  const [activeStep, setActiveStep] = useState<StepId>("identify");
  const [rows, setRows] = useState<EditorRow[]>([]);
  // Mirror rows into a ref so async flows (watchSummarize is kicked off at boot) read the CURRENT
  // rows, not the empty closure captured when the flow started.
  const rowsRef = useRef<EditorRow[]>([]);
  const applyRows = (next: EditorRow[]) => {
    rowsRef.current = next;
    setRows(next);
  };
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [totalPages, setTotalPages] = useState(0);
  const [, setStatus] = useState<DocumentStatus | "">("");
  const [filename, setFilename] = useState("");
  const [banner, setBanner] = useState("");
  const [watching, setWatching] = useState(false);
  const [startHint, setStartHint] = useState("");
  const [progress, setProgress] = useState({ title: "Working...", pct: 4, detail: "Starting..." });
  const [saveState, setSaveState] = useState<SaveState>({ kind: "" });
  // Mirrored into a ref for the same reason as `rows`: reloadRows is handed to another tab as a
  // callback and fires long after the render that created it, and it has to know whether this
  // buffer is holding unsaved work before it decides to replace it.
  const saveStateRef = useRef<SaveState>({ kind: "" });
  const applySaveState = (next: SaveState) => {
    saveStateRef.current = next;
    setSaveState(next);
  };
  // `_key:field` for every server-writable field the reviewer has changed since the last successful
  // save. reloadRows takes the server's `include`/`category` for everything EXCEPT these, so an
  // unsaved untick or re-classify is not reverted by an action on another tab. Cleared the moment a
  // save lands, because the server then holds those values itself and there is nothing to protect.
  const touchedRef = useRef<Set<string>>(new Set());
  const [header, setHeader] = useState<HeaderFields | null>(null);
  // A calm, non-error notice when a summarize run ended "needs attention" (item 7): the message
  // plus the sub-documents that failed, so the UI can list + highlight exactly which ones.
  const [attention, setAttention] = useState<{ message: string; rows: FailedRow[] } | null>(null);

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The job the poller last saw, so Stop can name it. A ref rather than state because it is read by
  // an event handler, never rendered - keeping it out of state avoids a re-render per poll tick.
  const activeJobRef = useRef<{ id: number; kind: JobKind } | null>(null);
  // The same id as activeJobRef, but as STATE so the view can react to the job changing under it. A
  // chained pipeline (segment -> dedup) swaps the active job while `watching` stays true the whole
  // time, and anything scoped to "this run" - the Stop escalation above all - has to reset on that
  // boundary. A ref cannot drive that, because it does not re-render.
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  // Set when a run settles as cancelled, and the only thing that puts the Continue / Start over pair
  // on screen. Carries the kind because each kind restarts through its own endpoint.
  const [cancelledJob, setCancelledJob] = useState<{ kind: JobKind } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function clearPoll() {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    pollTimer.current = null;
  }

  const enterEditor = () => {
    setSection("editor");
    setActiveStep("review");
  };
  const showSummaries = () => {
    setSection("summaries");
    setActiveStep("summaries");
  };
  const showStart = (hint = "") => {
    setStartHint(hint);
    setSection("start");
    setActiveStep("identify");
  };

  function pollJob(title: string, step: StepId): Promise<PollResult> {
    clearPoll();
    setActiveStep(step);
    setSection("progress");
    setProgress({ title, pct: 4, detail: "Starting..." });
    return new Promise((resolve, reject) => {
      const tick = async () => {
        let snap: Awaited<ReturnType<typeof getStatus>>;
        try {
          snap = await getStatus(documentId as string);
        } catch (err) {
          reject(err);
          return;
        }
        const job = snap.job;
        if (!job) {
          setActiveJobId(null);
          resolve({ outcome: "done" });
          return;
        }
        // Remember which job this is, so Stop can address it by id rather than "whatever is active".
        activeJobRef.current = { id: job.id, kind: job.kind };
        setActiveJobId(job.id);
        const pct = job.total ? Math.round((100 * job.current) / job.total) : 5;
        const label = STAGE_LABELS[job.stage] || job.stage || "Working";
        setProgress({
          title,
          pct: Math.max(pct, 4),
          detail: job.total ? `${label} (${job.current}/${job.total})` : label,
        });
        if (job.state === "done") return resolve({ outcome: "done" });
        if (job.state === "needs_attention")
          return resolve({
            outcome: "needs_attention",
            message: job.error || "Some documents need attention.",
            rows: job.attention?.rows ?? [],
          });
        if (job.state === "error") return reject(new Error(job.error || "the run failed"));
        if (job.state === "interrupted") return reject(new Error("the run was interrupted"));
        // A stop is NOT a rejection: the reviewer asked for it, so it resolves and the page offers
        // Continue / Start over instead of an error banner.
        if (job.state === "cancelled") return resolve({ outcome: "cancelled" });
        // queued / running / paused: keep polling. A paused run auto-resumes; its "paused" stage
        // label keeps the bar visible and reassuring rather than surfacing an error.
        pollTimer.current = setTimeout(tick, 1000);
      };
      void tick();
    });
  }

  /** Ask the running job to stop. `force` kills the work-horse; it is the second press, never the
   *  first. Safe to call when nothing is running - there is simply no job to name. */
  async function cancelActiveJob(force = false) {
    const job = activeJobRef.current;
    if (!documentId || !job) return 0;
    try {
      const { graceSeconds } = await cancelJob(documentId, job.id, force);
      return graceSeconds;
    } catch (err) {
      // The poll keeps running, so a failed request leaves the bar moving rather than lying about
      // having stopped. Say so instead of failing silently.
      setBanner(message(err, "could not stop the run"));
      return 0;
    }
  }

  /** Restart the kind that was cancelled. `fresh` is Start over; otherwise Continue. */
  async function restartCancelled(fresh: boolean) {
    const kind = cancelledJob?.kind;
    if (!documentId || !kind) return;
    setCancelledJob(null);
    try {
      if (kind === "summarize") await startSummarize(documentId, stripKeys(rowsRef.current), fresh);
      else if (kind === "dedup") await startDedup(documentId, fresh);
      else await startSegment(documentId, fresh);
      if (kind === "summarize") void watchSummarize();
      else void watchSegment();
    } catch (err) {
      setBanner(message(err, "could not restart the run"));
    }
  }

  async function watchSegment() {
    setWatching(true);
    setCancelledJob(null);
    try {
      const result = await pollJob("Identifying documents", "identify");
      if (result.outcome === "cancelled") {
        setWatching(false);
        setCancelledJob({ kind: activeJobRef.current?.kind ?? "segment" });
        // Whatever rows survive are the reviewer's to see; with none, the start screen is the only
        // honest thing to show.
        if (rowsRef.current.length) enterEditor();
        else showStart();
        return;
      }
      const detail = await getDocument(documentId as string);
      applyRows(sortRows(withKeys(detail.rows || [])));
      setStatus(detail.status);
      setHeader({
        patient_first_name: detail.patient_first_name || "",
        patient_last_name: detail.patient_last_name || "",
        patient_dob: detail.patient_dob || "",
        law_firm: detail.law_firm || "",
      });
      setWatching(false);
      enterEditor();
    } catch (err) {
      setWatching(false);
      setBanner(message(err, "identification failed"));
      showStart();
    }
  }

  async function watchSummarize() {
    setWatching(true);
    setCancelledJob(null);
    try {
      const result = await pollJob("Summarizing documents", "summaries");
      setWatching(false);
      // The run REPLACED the stored summaries, so drop the cached copy before any branch below
      // decides which screen to show. Here rather than in a branch because all three outcomes leave
      // new rows behind: "done" rewrote every included row, "needs_attention" keeps the partial
      // results, and even "cancelled" commits whatever finished before the stop - as the comment on
      // that branch says.
      //
      // Nothing else refreshes this query. It is a bare useQuery with the app-wide
      // `staleTime: 30_000` and `refetchOnWindowFocus: false`, no `refetchInterval`, and the only
      // other writer is the per-edit `setQueryData` patch - so its sole refresh trigger was a NEW
      // observer mounting while the data was stale. Every summarize path except one got that by
      // accident, by starting on a different tab and having the Summaries tab mount when the run
      // finished. "Re-summarize all from scratch" is the exception: the button only renders on the
      // Summaries tab, the tab body is not gated on `watching` so the view stays mounted for the
      // whole run, and `showSummaries()` then sets the tab to the value it already holds - so React
      // bails out, no observer mounts, and staleness alone never refetches in react-query.
      //
      // The consequence was worse than a stale read: `Summary.idx` is positional over included rows,
      // so editing a card from the stale view wrote the OLD body over the freshly generated one.
      if (documentId) {
        void queryClient.invalidateQueries({ queryKey: summariesKey(documentId) });
      }
      if (result.outcome === "cancelled") {
        setCancelledJob({ kind: "summarize" });
        // Summaries finished before the stop are already committed and stay visible.
        if (rowsRef.current.length) enterEditor();
        else showStart();
        return;
      }
      if (result.outcome === "needs_attention") {
        // Calm terminal state: some documents could not be summarized. Show the notice + the
        // editor (the reviewer fixes/excludes them, then summarizes again). Partial results kept.
        setAttention({
          message: result.message || "Some documents need attention.",
          rows: result.rows ?? [],
        });
        setStatus("needs_attention");
        if (rowsRef.current.length) enterEditor();
        else showStart();
        return;
      }
      setStatus("done");
      if (enableSummaries) showSummaries();
      else if (rowsRef.current.length) enterEditor();
      else showStart();
    } catch (err) {
      setWatching(false);
      setBanner(message(err, "summarization failed"));
      if (rows.length) enterEditor();
      else showStart();
    }
  }

  // Boot once per document id (StrictMode-safe: clearPoll makes the poll single-flight). A null id
  // is idle - nothing to boot until a document is selected.
  useEffect(() => {
    if (!documentId) {
      clearPoll();
      return;
    }
    let cancelled = false;
    async function boot() {
      let detail: Awaited<ReturnType<typeof getDocument>>;
      try {
        detail = await getDocument(documentId as string);
      } catch (err) {
        if (!cancelled) {
          setBanner(`Could not load this document: ${message(err, "error")}`);
          showStart();
        }
        return;
      }
      if (cancelled) return;
      setTotalPages(detail.page_count);
      setCategories(detail.categories || []);
      applyRows(sortRows(withKeys(detail.rows || [])));
      setStatus(detail.status);
      setFilename(detail.original_filename || "");
      setHeader({
        patient_first_name: detail.patient_first_name || "",
        patient_last_name: detail.patient_last_name || "",
        patient_dob: detail.patient_dob || "",
        law_firm: detail.law_firm || "",
      });

      const job = detail.active_job;
      if (job?.kind === "segment") return void watchSegment();
      if (job?.kind === "summarize") return void watchSummarize(); // covers queued/running/paused
      if (enableSummaries && detail.status === "done") return showSummaries();
      if (detail.status === "needs_attention") {
        // Reopened after a run that needs attention: recover the reason from the latest job.
        try {
          const snap = await getStatus(documentId as string);
          setAttention({
            message: snap.job?.error || "Some documents need attention.",
            rows: snap.job?.attention?.rows ?? [],
          });
        } catch {
          setAttention({ message: "Some documents need attention.", rows: [] });
        }
        if ((detail.rows || []).length) return enterEditor();
        return showStart();
      }
      if (detail.status === "error") setBanner("The last run failed - you can start again.");
      else if (detail.status === "interrupted")
        setBanner("The last run was interrupted - start again.");
      if ((detail.rows || []).length) return enterEditor();
      showStart();
    }
    void boot();
    return () => {
      cancelled = true;
      clearPoll();
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  function gotoStep(step: StepId) {
    if (watching) return; // a running job holds the screen; navigating would fight the auto-advance
    setBanner("");
    if (step === "identify") showStart();
    else if (step === "review") {
      if (rows.length) enterEditor();
      else showStart("No documents identified yet - run identification first.");
    } else {
      showSummaries();
    }
  }

  async function onStart() {
    if (!documentId) return;
    if (
      rows.length &&
      !window.confirm(
        "Re-running identification replaces the current document list AND your corrections. Continue?",
      )
    ) {
      return;
    }
    setBanner("");
    setAttention(null);
    try {
      await startSegment(documentId);
      await watchSegment();
    } catch (err) {
      setBanner(message(err, "Could not start identification."));
      showStart();
    }
  }

  // fresh=true is "Re-summarize all": clear prior summaries + regenerate every row. Default false
  // reuses done rows by identity (a re-click only fills the gaps / retries the failed ones).
  async function onSummarize(fresh = false) {
    if (!documentId) return;
    setBanner("");
    setAttention(null);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    try {
      await startSummarize(documentId, stripKeys(sortRows(rows)), fresh);
      await watchSummarize();
    } catch (err) {
      setBanner(message(err, "Could not start summarization."));
      enterEditor();
    }
  }

  // Pull the row changes another tab made server-side into the editor's local state - `include`
  // from Duplicates ("keep this one"), `category` from Summaries (re-classify) - so a subsequent
  // Summarize, which flushes these local rows, does not send the old values straight back.
  //
  // It used to do that by REPLACING the buffer, which discarded whatever the reviewer had typed but
  // not saved. Both callers live on other tabs, where the editor is unmounted, so nothing on screen
  // showed what was being thrown away; the header went on displaying "Not saved: ..." for a change
  // set that no longer existed. The exposure is not a race window either - the autosave sets
  // `error` and nothing retries until the next keystroke, so a failed save leaves the only copy of
  // those edits in this buffer indefinitely.
  //
  // So: replace wholesale only when there is nothing to lose. With unsaved edits present, apply the
  // two server-written fields onto the local rows instead and keep everything else. Flushing the
  // local rows first would be worse than either - the write that triggered this callback would be
  // overwritten by the stale copy, which is the bug this function exists to prevent.
  async function reloadRows() {
    if (!documentId) return;
    try {
      const detail = await getDocument(documentId);
      const server = detail.rows || [];
      const unsaved = saveStateRef.current.kind === "dirty" || saveStateRef.current.kind === "error";
      if (unsaved && rowsRef.current.length) {
        applyRows(sortRows(applyServerRowChanges(rowsRef.current, server, touchedRef.current)));
      } else {
        // Nothing local to protect, so the server is authoritative and the set is meaningless -
        // its keys refer to rows that no longer exist.
        touchedRef.current = new Set();
        applyRows(sortRows(withKeys(server)));
      }
    } catch {
      /* keep the current rows if the refresh fails */
    }
  }

  function onRowsChange(next: EditorRow[]) {
    if (!documentId) return;
    const sorted = sortRows(next);
    for (const key of touchedFields(rowsRef.current, sorted)) touchedRef.current.add(key);
    applyRows(sorted);
    applySaveState({ kind: "dirty", message: "Unsaved changes..." });
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      if (!sorted.length) return; // nothing to save yet (transient mid-edit)
      if (rowErrors(sorted, totalPages).size) {
        // Don't silently leave changes unsaved: tell the user why (and Summarize stays blocked).
        applySaveState({ kind: "error", message: "Not saved - fix the highlighted page ranges first." });
        return;
      }
      saveRows(documentId, stripKeys(sorted))
        .then(() => {
          touchedRef.current = new Set();
          applySaveState({ kind: "saved" });
        })
        .catch((err) =>
          applySaveState({
            kind: "error",
            message: `Not saved: ${humanizeError(err, { fallback: "please try again" })}`,
          }),
        );
    }, 800);
  }

  return {
    section,
    activeStep,
    rows,
    categories,
    totalPages,
    filename,
    banner,
    setBanner,
    watching,
    startHint,
    progress,
    saveState,
    header,
    setHeader,
    attention,
    // The stop/restart trio. `cancelledJob` is non-null ONLY after a run settles as cancelled, which
    // is what puts the Continue / Start over pair on screen; the progress bar has unmounted by then.
    cancelledJob,
    // Which job the progress bar is currently showing. The view resets per-run controls when this
    // changes, so a grace period that expired on the previous job cannot carry into the next one.
    activeJobId,
    cancelActiveJob,
    restartCancelled,
    onStart,
    onSummarize,
    onRowsChange,
    reloadRows,
    gotoStep,
  };
}
