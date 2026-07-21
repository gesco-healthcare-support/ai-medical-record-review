"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  getDocument,
  getStatus,
  saveRows,
  startSegment,
  startSummarize,
  type HeaderFields,
} from "@/lib/review-api";
import type { CategoryOption, DocumentStatus } from "@/lib/types";
import { rowErrors, sortRows, stripKeys, withKeys, type EditorRow } from "@/lib/review-rows";
import type { StepId } from "@/components/review/stepper";

export type Section = "loading" | "start" | "progress" | "editor" | "summaries";

/** Autosave indicator state for the review header. */
export type SaveState = { kind: "" | "saved" | "dirty" | "error"; message?: string };

const STAGE_LABELS: Record<string, string> = {
  starting: "Starting...",
  segmenting: "Reading the record and finding document boundaries",
  categorizing: "Categorizing each document",
  verifying: "Double-checking uncertain boundaries",
  summarizing: "Writing summaries",
  paused: "Paused - waiting for capacity, will retry automatically",
};

/** How a polled job settled: finished cleanly, or ended needing the reviewer's attention. */
type PollResult = { outcome: "done" | "needs_attention"; message?: string };

function message(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback;
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

  const [section, setSection] = useState<Section>("loading");
  const [activeStep, setActiveStep] = useState<StepId>("identify");
  const [rows, setRows] = useState<EditorRow[]>([]);
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [totalPages, setTotalPages] = useState(0);
  const [, setStatus] = useState<DocumentStatus | "">("");
  const [filename, setFilename] = useState("");
  const [banner, setBanner] = useState("");
  const [watching, setWatching] = useState(false);
  const [startHint, setStartHint] = useState("");
  const [progress, setProgress] = useState({ title: "Working...", pct: 4, detail: "Starting..." });
  const [saveState, setSaveState] = useState<SaveState>({ kind: "" });
  const [header, setHeader] = useState<HeaderFields | null>(null);
  // A calm, non-error notice when a summarize run ended "needs attention" (item 7).
  const [attention, setAttention] = useState<{ message: string } | null>(null);

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
          resolve({ outcome: "done" });
          return;
        }
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
          });
        if (job.state === "error") return reject(new Error(job.error || "the run failed"));
        if (job.state === "interrupted") return reject(new Error("the run was interrupted"));
        // queued / running / paused: keep polling. A paused run auto-resumes; its "paused" stage
        // label keeps the bar visible and reassuring rather than surfacing an error.
        pollTimer.current = setTimeout(tick, 1000);
      };
      void tick();
    });
  }

  async function watchSegment() {
    setWatching(true);
    try {
      await pollJob("Identifying documents", "identify");
      const detail = await getDocument(documentId as string);
      setRows(sortRows(withKeys(detail.rows || [])));
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
    try {
      const result = await pollJob("Summarizing documents", "summaries");
      setWatching(false);
      if (result.outcome === "needs_attention") {
        // Calm terminal state: some documents could not be summarized. Show the notice + the
        // editor (the reviewer fixes/excludes them, then summarizes again). Partial results kept.
        setAttention({ message: result.message || "Some documents need attention." });
        setStatus("needs_attention");
        if (rows.length) enterEditor();
        else showStart();
        return;
      }
      setStatus("done");
      if (enableSummaries) showSummaries();
      else if (rows.length) enterEditor();
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
      setRows(sortRows(withKeys(detail.rows || [])));
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
          setAttention({ message: snap.job?.error || "Some documents need attention." });
        } catch {
          setAttention({ message: "Some documents need attention." });
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

  function onRowsChange(next: EditorRow[]) {
    if (!documentId) return;
    const sorted = sortRows(next);
    setRows(sorted);
    setSaveState({ kind: "dirty", message: "Unsaved changes..." });
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      if (!sorted.length || rowErrors(sorted, totalPages).size) return; // invalid states stay local
      saveRows(documentId, stripKeys(sorted))
        .then(() => setSaveState({ kind: "saved" }))
        .catch((err) =>
          setSaveState({
            kind: "error",
            message: `Not saved: ${err instanceof ApiError ? err.message : "error"}`,
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
    onStart,
    onSummarize,
    onRowsChange,
    gotoStep,
  };
}
