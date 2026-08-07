"use client";

import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { rowErrors } from "@/lib/review-rows";
import { humanizeError } from "@/lib/errors";
import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { useSummaries } from "@/hooks/use-summaries";
import { useDuplicates, useStartDedup } from "@/hooks/use-duplicates";
import { SegmentedTabs } from "@/components/ui/segmented-tabs";
import { BackLink } from "@/components/app/back-link";
import { ReviewEditor } from "./review-editor";
import { SummariesView } from "./summaries-view";
import { DuplicatesView } from "./duplicates-view";
import { HeaderBar } from "./header-bar";
import { StartPanel } from "./start-panel";
import { ProgressPanel } from "./progress-panel";

type Tab = "review" | "duplicates" | "summaries";

/** The /records/[id] workbench: one slim header (back, record name + count, SegmentedTabs, autosave,
 *  Auto-fill / Segment / Summarize) over a tab body - the always-on Review & correct editor or the
 *  Summaries view. The identify/summarize lifecycle lives in useReviewWorkflow; a running job turns
 *  the header actions into an inline progress bar and dims the editor. */
export function ReviewPageClient({ documentId }: { documentId: string }) {
  const wf = useReviewWorkflow(documentId);
  const { data: summaries = [] } = useSummaries(documentId);
  const { data: dupData } = useDuplicates(documentId);
  const recheck = useStartDedup(documentId);
  const [tab, setTab] = useState<Tab>("review");
  // A cluster still needs the reviewer while 2+ of its copies would be summarized - the same rule the
  // API's advisory count and the cluster chip use, so every surface agrees.
  const unresolvedDupes = (dupData?.clusters ?? []).filter(
    (c) => !c.dismissed && c.rows.filter((r) => r.include !== false).length >= 2,
  ).length;
  // A dedup job blocks both /dedup/start and /summarize/start server-side (409), so disable rather
  // than surface the conflict.
  const dedupRunning = dupData?.job?.state === "queued" || dupData?.job?.state === "running";
  // A per-copy removal leaves no trace in the response (the row simply has no group), so there is no
  // way to detect one. Gate the re-check warning on clusters existing at all: the first-ever check has
  // nothing to lose, and once groups are on screen the reviewer may have curated them.
  const hasClusters = (dupData?.clusters ?? []).length > 0;
  const lastSection = useRef(wf.section);

  // The hook lands on "summaries" after a summarize job finishes (or when a done record boots);
  // follow it to the Summaries tab, but leave manual tab switches alone afterward.
  useEffect(() => {
    if (wf.section === "summaries" && lastSection.current !== "summaries") setTab("summaries");
    lastSection.current = wf.section;
  }, [wf.section]);

  // A needs_attention run highlights the failed rows in the editor, so surface the Review tab when
  // the notice appears (the user may have been on Summaries when the run finished).
  useEffect(() => {
    if (wf.attention) setTab("review");
  }, [wf.attention]);

  const errors = rowErrors(wf.rows, wf.totalPages);
  const included = wf.rows.filter((r) => r.include !== false).length;

  // The sub-documents a needs_attention run could not summarize, keyed by page range for matching
  // to editor rows (the idx in attention is the included-position, not review_row.idx - match on
  // pages).
  const failedRows = wf.attention?.rows ?? [];
  const attentionPages = new Set(failedRows.map((r) => r.pages));
  const titleByPages = new Map(
    wf.rows.map((r) => [`${r.start}-${r.end}`, r.title && r.title !== "-" ? r.title : ""]),
  );

  const tabs = [
    { value: "review" as const, label: "Review & correct" },
    {
      value: "duplicates" as const,
      label: unresolvedDupes ? `Duplicates · ${unresolvedDupes}` : "Duplicates",
    },
    {
      value: "summaries" as const,
      label: summaries.length ? `Summaries · ${summaries.length}` : "Summaries",
    },
  ];

  const save = wf.saveState;
  // The paused stage label is stable (STAGE_LABELS.paused); style the bar distinctly while waiting.
  const paused = wf.watching && wf.progress.detail.toLowerCase().startsWith("paused");

  // Stop is two-stage. The first press is cooperative and normally lands within a second; only if the
  // run has not acknowledged it after the SERVER's grace period does the button escalate to a hard
  // kill, because a force stop can land mid-transaction and leaves orphan recovery to tidy up.
  const [stopping, setStopping] = useState(false);
  const [forceReady, setForceReady] = useState(false);
  // The pending escalation, held so it can be cancelled. A run that stops INSIDE the grace period
  // would otherwise leave this timer to fire after the reset below, stranding the button on "Force
  // stop" - making the first press on the NEXT job a hard kill the reviewer never asked for.
  const forceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function clearForceTimer() {
    if (forceTimer.current) {
      clearTimeout(forceTimer.current);
      forceTimer.current = null;
    }
  }

  // Reset on the JOB boundary, not only when watching ends. Segmentation chains straight into the
  // duplicate check, so the active job changes while `watching` stays true throughout - and keying
  // only on `watching` left a grace period that expired on the finished job showing "Force stop" as
  // the NEXT job's first state. Reproduced live: Stop at 0.2s, escalation at 10s, then the chained
  // job appeared at 15.6s already offering a hard kill nobody had asked for.
  useEffect(() => {
    setStopping(false);
    setForceReady(false);
    clearForceTimer();
  }, [wf.watching, wf.activeJobId]);

  // Unmounting mid-stop must not leave a timer that sets state on a dead component.
  useEffect(() => clearForceTimer, []);

  async function onStop() {
    if (forceReady) {
      void wf.cancelActiveJob(true);
      return;
    }
    setStopping(true);
    const graceSeconds = await wf.cancelActiveJob(false);
    if (graceSeconds > 0) {
      clearForceTimer(); // never stack two escalations from a double press
      forceTimer.current = setTimeout(() => setForceReady(true), graceSeconds * 1000);
    }
  }

  /** Continue after a stop, warning first if it would discard reviewer edits.
   *
   *  A summarize resume keys on (start, end, category), so a summary whose row was re-classified since
   *  it was written no longer matches and is deleted and regenerated. That is correct - the category
   *  changed - but it takes the reviewer's edits with it, so it must not happen silently. */
  async function onRestart(fresh: boolean) {
    if (!fresh && wf.cancelledJob?.kind === "summarize") {
      const atRisk = (summaries ?? []).filter(
        (s) => s.edited && s.rowCategoryLive !== null && s.rowCategoryLive !== s.row.category,
      ).length;
      if (
        atRisk > 0 &&
        !window.confirm(
          `${atRisk} summar${atRisk === 1 ? "y" : "ies"} you edited will be rewritten from scratch, ` +
            "because the category changed since they were written. Your edits to those will be lost. Continue?",
        )
      ) {
        return;
      }
    }
    void wf.restartCancelled(fresh);
  }
  // Block Summarize while any row is invalid, nothing is selected, a save failed/is pending, or a
  // duplicate check is running - so a user never summarizes stale or invalid rows.
  const summarizeDisabled =
    errors.size > 0 ||
    included === 0 ||
    save.kind === "error" ||
    save.kind === "dirty" ||
    dedupRunning;

  // Un-nested reason for the disabled "Check duplicates" button, same rule as summarizeHint below.
  let checkDuplicatesHint: string | undefined;
  if (save.kind === "dirty") checkDuplicatesHint = "Your latest changes aren't saved yet.";
  else if (dedupRunning) checkDuplicatesHint = "A duplicate check is already running.";
  else checkDuplicatesHint = undefined;

  // Un-nested reason for the disabled Summarize button (Sonar S3358: no nested ternary in JSX).
  let summarizeHint: string | undefined;
  if (!summarizeDisabled) summarizeHint = undefined;
  else if (errors.size > 0) summarizeHint = "Fix the highlighted page ranges before summarizing.";
  else if (included === 0) summarizeHint = "Select at least one document to summarize.";
  else if (dedupRunning) summarizeHint = "Wait for the duplicate check to finish.";
  else summarizeHint = "Your latest changes aren't saved yet.";

  // Summarize lives on the Duplicates step, so the reasons it is blocked have to be readable THERE
  // too - otherwise the reviewer faces a disabled button whose only explanation is a hover tooltip.
  // Summaries has no Summarize button, so the same banners would be noise on that tab.
  const showSummarizeBlockers = tab === "review" || tab === "duplicates";

  const onRecheck = async () => {
    // A re-check reclusters from scratch and re-applies a dismissal only to a cluster holding exactly
    // the same copies, so per-copy removals do not survive it - a pruned group comes back intact.
    // Warn only when there is something to lose.
    if (
      hasClusters &&
      !window.confirm(
        "Re-checking finds duplicate groups again from scratch. Copies you removed from a group " +
          "individually will be asked about again. Continue?",
      )
    ) {
      return;
    }
    wf.setBanner("");
    try {
      await recheck.mutateAsync();
    } catch (err) {
      wf.setBanner(
        humanizeError(err, { fallback: "Could not start the check - please try again." }),
      );
    }
  };

  // Starting the FIRST duplicate check, from the Review step. Deliberately no confirm dialog:
  // onRecheck warns about losing per-copy removals, and on a first run there is nothing to lose.
  const onCheckDuplicates = async () => {
    wf.setBanner("");
    try {
      await recheck.mutateAsync();
      setTab("duplicates");
    } catch (err) {
      // Stay on Review so the banner is where the reviewer is already looking.
      wf.setBanner(humanizeError(err, { fallback: "Could not start the check - please try again." }));
    }
  };

  // The only control that regenerates EVERY summary from scratch - the one to use after a prompt
  // change, since a plain Summarize keeps summaries whose page range and category are unchanged.
  const reSummarizeAll = () => {
    if (
      window.confirm(
        `Regenerate all ${summaries.length} summaries from scratch with the current prompts? ` +
          "Every current summary, including your edits to them, is discarded and re-written by the AI.",
      )
    ) {
      void wf.onSummarize(true);
    }
  };

  return (
    <div className="rce">
      <header className="rce-bar">
        <div className="rce-bar-main">
          <BackLink />
          <div className="rce-title">
            <span className="rce-name">{wf.filename || "Record"}</span>
            <span className="rce-count">
              {wf.rows.length} document{wf.rows.length === 1 ? "" : "s"} · {wf.totalPages} page
              {wf.totalPages === 1 ? "" : "s"}
            </span>
          </div>
        </div>

        <SegmentedTabs tabs={tabs} value={tab} onValueChange={setTab} ariaLabel="Editor view" />

        <div className="rce-bar-actions">
          {wf.watching ? (
            <div className={cn("rce-progress", paused && "paused")} role="status" aria-live="polite">
              <span className="rce-progress-label">{wf.progress.detail}</span>
              <div className="rce-progress-bar">
                <div style={{ width: `${wf.progress.pct}%` }} />
              </div>
              <span className="rce-progress-pct">{wf.progress.pct}%</span>
              {/* Stop lives HERE, not in ProgressPanel: that panel only renders on a first segment
                  run (no rows yet), so a Stop there would be invisible for exactly the long
                  summarize a reviewer most wants to kill. */}
              <button
                type="button"
                className="ev-btn ev-btn-ghost ev-btn-sm rce-stop"
                onClick={onStop}
                title={
                  forceReady
                    ? "This run has not acknowledged the stop; force it to end now"
                    : "Ask this run to stop at its next safe point"
                }
              >
                {forceReady ? "Force stop" : stopping ? "Stopping..." : "Stop"}
              </button>
            </div>
          ) : (
            <>
              {tab === "review" && save.kind ? (
                <span className={cn("rc-save", save.kind)}>
                  {save.kind === "saved" ? (
                    <>
                      <Check width={14} height={14} aria-hidden /> Saved
                    </>
                  ) : (
                    save.message
                  )}
                </span>
              ) : null}
              {/* Each tab carries its own step's actions: correct the documents, then clear the
                  duplicates, then summarize - so the reviewer passes the duplicates gate. */}
              {tab === "review" ? (
                <>
                  {/* Re-segmenting discards every row correction AND /segment/start returns 409
                      while a dedup job holds the document lock - so it must not look clickable
                      mid-check. */}
                  <button
                    type="button"
                    className="ev-btn ev-btn-outline"
                    disabled={dedupRunning}
                    title={dedupRunning ? "Wait for the duplicate check to finish." : undefined}
                    onClick={wf.onStart}
                  >
                    {wf.rows.length ? "Re-run segment" : "Segment"}
                  </button>
                  {/* Starts the check, then shows the tab. Blocked on unsaved edits: dedup reads
                      include=True server-side, so scanning against unsaved checkbox changes would
                      check the wrong rows - the exact waste this gate exists to prevent. */}
                  <button
                    type="button"
                    className="ev-btn ev-btn-primary"
                    disabled={dedupRunning || recheck.isPending || save.kind === "dirty"}
                    title={checkDuplicatesHint}
                    onClick={onCheckDuplicates}
                  >
                    {recheck.isPending ? "Starting..." : "Check duplicates"}
                  </button>
                </>
              ) : null}
              {tab === "duplicates" ? (
                <>
                  <button
                    type="button"
                    className="ev-btn ev-btn-outline"
                    disabled={recheck.isPending || dedupRunning}
                    onClick={onRecheck}
                  >
                    {recheck.isPending ? "Starting..." : "Re-check duplicates"}
                  </button>
                  <button
                    type="button"
                    className="ev-btn ev-btn-primary"
                    disabled={summarizeDisabled}
                    title={summarizeHint}
                    onClick={() => wf.onSummarize()}
                  >
                    {included
                      ? `Summarize ${included} document${included === 1 ? "" : "s"}`
                      : "Summarize"}
                  </button>
                </>
              ) : null}
              {tab === "summaries" && summaries.length > 0 ? (
                <button
                  type="button"
                  className="ev-btn ev-btn-ghost"
                  title="Regenerates every summary from scratch with the current prompts, discarding your edits. Use this after a prompt change."
                  onClick={reSummarizeAll}
                >
                  Re-summarize all from scratch
                </button>
              ) : null}
            </>
          )}
        </div>
      </header>

      {wf.banner ? <div className="banner">{wf.banner}</div> : null}
      {/* The post-stop choice lives HERE rather than in the progress bar, because that bar unmounts
          the moment the job stops being active and so cannot host it. */}
      {wf.cancelledJob ? (
        <div className="banner" role="status">
          <strong>Stopped.</strong> Anything already finished has been kept.{" "}
          <button
            type="button"
            className="ev-btn ev-btn-primary ev-btn-sm"
            onClick={() => onRestart(false)}
          >
            Continue
          </button>{" "}
          <button
            type="button"
            className="ev-btn ev-btn-outline ev-btn-sm"
            onClick={() => onRestart(true)}
          >
            Start over
          </button>
        </div>
      ) : null}
      {unresolvedDupes > 0 && tab !== "duplicates" ? (
        <div className="banner" role="status">
          {unresolvedDupes} possible duplicate {unresolvedDupes === 1 ? "group" : "groups"} to
          review before summarizing.{" "}
          <button
            type="button"
            className="ev-btn ev-btn-ghost ev-btn-sm"
            onClick={() => setTab("duplicates")}
          >
            Review duplicates
          </button>
        </div>
      ) : null}
      {wf.attention ? (
        <div className="notice-attention" role="status">
          <p>{wf.attention.message}</p>
          {failedRows.length ? (
            <ul className="notice-attention-list">
              {failedRows.map((r) => {
                const title = titleByPages.get(r.pages);
                return (
                  <li key={r.pages}>
                    <strong>
                      Pages {r.pages}
                      {title ? ` - ${title}` : ""}:
                    </strong>{" "}
                    {r.reason}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
      {showSummarizeBlockers && save.kind === "error" && errors.size === 0 ? (
        <div className="banner" role="alert">
          {save.message}
        </div>
      ) : null}
      {showSummarizeBlockers && errors.size > 0 ? (
        <div className="banner" aria-live="polite">
          <strong>Fix these before summarizing:</strong>
          <ul>
            {[...errors.entries()].map(([i, msg]) => (
              <li key={i}>
                Document {i + 1}: {msg}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rce-body">
        {tab === "review" ? (
          wf.rows.length === 0 && wf.watching ? (
            <ProgressPanel
              title={wf.progress.title}
              pct={wf.progress.pct}
              detail={wf.progress.detail}
            />
          ) : wf.rows.length === 0 ? (
            <StartPanel rerun={false} hint={wf.startHint} onStart={wf.onStart} />
          ) : (
            <>
              <HeaderBar documentId={documentId} header={wf.header} onSaved={(f) => wf.setHeader(f)} />
              <div className={cn("rce-editor", wf.watching && "busy")}>
                <ReviewEditor
                  documentId={documentId}
                  filename={wf.filename}
                  rows={wf.rows}
                  categories={wf.categories}
                  totalPages={wf.totalPages}
                  onRowsChange={wf.onRowsChange}
                  attentionPages={attentionPages}
                />
              </div>
            </>
          )
        ) : tab === "duplicates" ? (
          <DuplicatesView
            documentId={documentId}
            filename={wf.filename}
            onResolved={wf.reloadRows}
          />
        ) : (
          <SummariesView
            documentId={documentId}
            filename={wf.filename}
            categories={wf.categories}
            header={wf.header}
            onHeaderSaved={wf.setHeader}
            onGotoSummarizeStep={() => setTab("duplicates")}
            onRowsChanged={wf.reloadRows}
          />
        )}
      </div>
    </div>
  );
}
