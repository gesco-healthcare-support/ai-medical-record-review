"use client";

import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { couldNotIdentify, rowErrors } from "@/lib/review-rows";
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
/** The stop button's three states. Top-level so its branches do not count against the page
 *  component, which is what this file is being trimmed for. */
function stopButtonLabel(forceReady: boolean, stopping: boolean) {
  if (forceReady) return "Force stop";
  if (stopping) return "Stopping...";
  return "Stop";
}

/** Why "Check duplicates" is disabled, or undefined when it is not. */
function checkDuplicatesReason(saveKind: string, dedupRunning: boolean) {
  if (saveKind === "dirty") return "Your latest changes aren't saved yet.";
  if (dedupRunning) return "A duplicate check is already running.";
  return undefined;
}

/** Why Summarize is disabled, or undefined when it is not. An if-chain rather than nested ternaries,
 *  per Sonar S3358 - the same shape this logic already had inline; only its home has changed. */
function summarizeReason(
  o: Readonly<{
    disabled: boolean;
    errorCount: number;
    included: number;
    dedupRunning: boolean;
    needsDuplicateCheck: boolean;
    hasChecked: boolean;
  }>,
) {
  if (!o.disabled) return undefined;
  if (o.errorCount > 0)
    return "Fix the highlighted page ranges before summarizing.";
  if (o.included === 0) return "Select at least one document to summarize.";
  if (o.dedupRunning) return "Wait for the duplicate check to finish.";
  if (o.needsDuplicateCheck && o.hasChecked)
    return "The documents changed since the last duplicate check.";
  if (o.needsDuplicateCheck)
    return "This record has not been checked for duplicates yet.";
  return "Your latest changes aren't saved yet.";
}

/** The record header's count line, e.g. "3 documents · 12 pages". The separator is written as an
 *  escape so this file stays ASCII; it renders as the same middot it always did. */
function recordCountLabel(rowCount: number, totalPages: number) {
  const documents = rowCount === 1 ? "document" : "documents";
  const pages = totalPages === 1 ? "page" : "pages";
  return `${rowCount} ${documents} · ${totalPages} ${pages}`;
}

/** The inline progress bar that replaces the header actions while a job runs. This and each step
 *  component below own their own visibility guard and return null, so the page component holds no
 *  conditional for them at all - that is the point of the split, not a side effect of it. */
function RunningProgress({
  watching,
  progress,
  paused,
  stopping,
  forceReady,
  onStop,
}: Readonly<{
  watching: boolean;
  progress: { title: string; pct: number; detail: string };
  paused: boolean;
  stopping: boolean;
  forceReady: boolean;
  onStop: () => void;
}>) {
  if (!watching) return null;
  return (
    <div
      className={cn("rce-progress", paused && "paused")}
      role="status"
      aria-live="polite"
    >
      <span className="rce-progress-label">{progress.detail}</span>
      <div className="rce-progress-bar">
        <div style={{ width: `${progress.pct}%` }} />
      </div>
      <span className="rce-progress-pct">{progress.pct}%</span>
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
        {stopButtonLabel(forceReady, stopping)}
      </button>
    </div>
  );
}

/** The autosave chip. Review step only, and never while a job is running. */
function SaveChip({
  watching,
  tab,
  save,
}: Readonly<{
  watching: boolean;
  tab: Tab;
  save: { kind: string; message?: string };
}>) {
  if (watching || tab !== "review" || !save.kind) return null;
  return (
    <span className={cn("rc-save", save.kind)}>
      {save.kind === "saved" ? (
        <>
          <Check width={14} height={14} aria-hidden /> Saved
        </>
      ) : (
        save.message
      )}
    </span>
  );
}

/** Step one: correct the documents, then start the duplicate check. */
function ReviewStepActions({
  watching,
  tab,
  rowCount,
  dedupRunning,
  recheckPending,
  saveKind,
  checkDuplicatesHint,
  onStart,
  onCheckDuplicates,
}: Readonly<{
  watching: boolean;
  tab: Tab;
  rowCount: number;
  dedupRunning: boolean;
  recheckPending: boolean;
  saveKind: string;
  checkDuplicatesHint: string | undefined;
  onStart: () => void;
  onCheckDuplicates: () => void;
}>) {
  if (watching || tab !== "review") return null;
  return (
    <>
      {/* Re-segmenting discards every row correction AND /segment/start returns 409
          while a dedup job holds the document lock - so it must not look clickable
          mid-check. */}
      <button
        type="button"
        className="ev-btn ev-btn-outline"
        disabled={dedupRunning}
        title={
          dedupRunning ? "Wait for the duplicate check to finish." : undefined
        }
        onClick={onStart}
      >
        {rowCount ? "Re-run segment" : "Segment"}
      </button>
      {/* Starts the check, then shows the tab. Blocked on unsaved edits: dedup reads
          include=True server-side, so scanning against unsaved checkbox changes would
          check the wrong rows - the exact waste this gate exists to prevent. */}
      <button
        type="button"
        className="ev-btn ev-btn-primary"
        disabled={dedupRunning || recheckPending || saveKind === "dirty"}
        title={checkDuplicatesHint}
        onClick={onCheckDuplicates}
      >
        {recheckPending ? "Starting..." : "Check duplicates"}
      </button>
    </>
  );
}

/** Step two: clear the duplicates, then summarize. The skip control's six-term condition is
 *  computed HERE rather than passed in, so its cost sits in this component and not the page. */
function DuplicatesStepActions({
  watching,
  tab,
  recheckPending,
  dedupRunning,
  summarizeDisabled,
  summarizeHint,
  included,
  documentNoun,
  needsDuplicateCheck,
  errorCount,
  saveKind,
  onRecheck,
  onSummarize,
  onSummarizeWithoutChecking,
}: Readonly<{
  watching: boolean;
  tab: Tab;
  recheckPending: boolean;
  dedupRunning: boolean;
  summarizeDisabled: boolean;
  summarizeHint: string | undefined;
  included: number;
  documentNoun: string;
  needsDuplicateCheck: boolean | undefined;
  errorCount: number;
  saveKind: string;
  onRecheck: () => void;
  onSummarize: () => void;
  onSummarizeWithoutChecking: () => void;
}>) {
  if (watching || tab !== "duplicates") return null;
  // Only when the duplicate gate is the ONLY thing in the way - offering it while rows are
  // invalid or unsaved would let a reviewer skip past a different problem entirely.
  const canSkipCheck =
    needsDuplicateCheck &&
    errorCount === 0 &&
    included > 0 &&
    !dedupRunning &&
    saveKind !== "dirty" &&
    saveKind !== "error";
  return (
    <>
      <button
        type="button"
        className="ev-btn ev-btn-outline"
        disabled={recheckPending || dedupRunning}
        onClick={onRecheck}
      >
        {recheckPending ? "Starting..." : "Re-check duplicates"}
      </button>
      <button
        type="button"
        className="ev-btn ev-btn-primary"
        disabled={summarizeDisabled}
        title={summarizeHint}
        onClick={onSummarize}
      >
        {included ? `Summarize ${included} ${documentNoun}` : "Summarize"}
      </button>
      {canSkipCheck ? (
        <button
          type="button"
          className="ev-btn ev-btn-ghost"
          title="Proceed without checking this record for duplicates"
          onClick={onSummarizeWithoutChecking}
        >
          Summarize without checking
        </button>
      ) : null}
    </>
  );
}

/** Step three: the full regeneration, offered only once summaries exist. */
function SummariesStepActions({
  watching,
  tab,
  summariesCount,
  onReSummarizeAll,
}: Readonly<{
  watching: boolean;
  tab: Tab;
  summariesCount: number;
  onReSummarizeAll: () => void;
}>) {
  if (watching || tab !== "summaries" || summariesCount === 0) return null;
  return (
    <button
      type="button"
      className="ev-btn ev-btn-ghost"
      title="Regenerates every summary from scratch with the current prompts, discarding your edits. Use this after a prompt change."
      onClick={onReSummarizeAll}
    >
      Re-summarize all from scratch
    </button>
  );
}

/** The tab body: the first-run progress panel, the start panel, the editor, or whichever of the
 *  other two tabs is open. Extracted whole because its five conditionals only choose WHICH panel
 *  to show - none of them is logic the page component needs to own. */
function ReviewBody({
  tab,
  wf,
  documentId,
  attentionPages,
  onGotoSummarizeStep,
}: Readonly<{
  tab: Tab;
  wf: ReturnType<typeof useReviewWorkflow>;
  documentId: string;
  attentionPages: Set<string>;
  onGotoSummarizeStep: () => void;
}>) {
  return (
    <div className="rce-body">
      {tab === "review" && wf.rows.length === 0 && wf.watching ? (
        <ProgressPanel
          title={wf.progress.title}
          pct={wf.progress.pct}
          detail={wf.progress.detail}
        />
      ) : null}
      {tab === "review" && wf.rows.length === 0 && !wf.watching ? (
        <StartPanel rerun={false} hint={wf.startHint} onStart={wf.onStart} />
      ) : null}
      {tab === "review" && wf.rows.length > 0 ? (
        <>
          <HeaderBar
            documentId={documentId}
            header={wf.header}
            onSaved={(f) => wf.setHeader(f)}
          />
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
      ) : null}
      {tab === "duplicates" ? (
        <DuplicatesView
          documentId={documentId}
          filename={wf.filename}
          onResolved={wf.reloadRows}
        />
      ) : null}
      {tab === "summaries" ? (
        <SummariesView
          documentId={documentId}
          filename={wf.filename}
          categories={wf.categories}
          header={wf.header}
          onHeaderSaved={wf.setHeader}
          onGotoSummarizeStep={onGotoSummarizeStep}
          onRowsChanged={wf.reloadRows}
        />
      ) : null}
    </div>
  );
}

/** Every banner the review page can show, in the order it shows them.
 *
 *  Extracted from the page component for one reason: six conditionals nested inside a 500-line
 *  function dominated its complexity, and a conditional costs more the deeper it sits. Kept in this
 *  file rather than given its own, per the decision recorded in the cleanup plan. */
function ReviewBanners({
  wf,
  tab,
  unresolvedDupes,
  failedRows,
  titleByPages,
  showSummarizeBlockers,
  save,
  errors,
  onRestart,
  onReviewDuplicates,
}: Readonly<{
  wf: ReturnType<typeof useReviewWorkflow>;
  tab: Tab;
  unresolvedDupes: number;
  failedRows: { pages: string; reason: string }[];
  titleByPages: Map<string, string>;
  showSummarizeBlockers: boolean;
  save: { kind: string; message?: string };
  errors: Map<number, string>;
  onRestart: (fresh: boolean) => void;
  onReviewDuplicates: () => void;
}>) {
  return (
    <>
      {wf.banner ? <div className="banner">{wf.banner}</div> : null}
      {/* The post-stop choice lives HERE rather than in the progress bar, because that bar unmounts
          the moment the job stops being active and so cannot host it. */}
      {wf.cancelledJob ? (
        <output className="banner">
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
        </output>
      ) : null}
      {unresolvedDupes > 0 && tab !== "duplicates" ? (
        <output className="banner">
          {unresolvedDupes} possible duplicate{" "}
          {unresolvedDupes === 1 ? "group" : "groups"} to review before
          summarizing.{" "}
          <button
            type="button"
            className="ev-btn ev-btn-ghost ev-btn-sm"
            onClick={onReviewDuplicates}
          >
            Review duplicates
          </button>
        </output>
      ) : null}
      {wf.attention ? (
        <output className="notice-attention">
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
        </output>
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
    </>
  );
}

export function ReviewPageClient({
  documentId,
}: Readonly<{ documentId: string }>) {
  const wf = useReviewWorkflow(documentId);
  const { data: summaries = [] } = useSummaries(documentId);
  const { data: dupData } = useDuplicates(documentId);
  const recheck = useStartDedup(documentId);
  const [tab, setTab] = useState<Tab>("review");
  // A cluster still needs the reviewer while 2+ of its copies would be summarized - the same rule the
  // API's advisory count and the cluster chip use, so every surface agrees.
  const unresolvedDupes = (dupData?.clusters ?? []).filter(
    (c) =>
      !c.dismissed && c.rows.filter((r) => r.include !== false).length >= 2,
  ).length;
  // A dedup job blocks both /dedup/start and /summarize/start server-side (409), so disable rather
  // than surface the conflict.
  const dedupRunning =
    dupData?.job?.state === "queued" || dupData?.job?.state === "running";
  // A per-copy removal leaves no trace in the response (the row simply has no group), so there is no
  // way to detect one. Gate the re-check warning on clusters existing at all: the first-ever check has
  // nothing to lose, and once groups are on screen the reviewer may have curated them.
  const hasClusters = (dupData?.clusters ?? []).length > 0;
  const lastSection = useRef(wf.section);

  // The hook lands on "summaries" after a summarize job finishes (or when a done record boots);
  // follow it to the Summaries tab, but leave manual tab switches alone afterward.
  useEffect(() => {
    if (wf.section === "summaries" && lastSection.current !== "summaries")
      setTab("summaries");
    lastSection.current = wf.section;
  }, [wf.section]);

  // A needs_attention run highlights the failed rows in the editor, so surface the Review tab when
  // the notice appears (the user may have been on Summaries when the run finished).
  useEffect(() => {
    if (wf.attention) setTab("review");
  }, [wf.attention]);

  const errors = rowErrors(wf.rows, wf.totalPages);
  const included = wf.rows.filter((r) => r.include !== false).length;
  const documentNoun = included === 1 ? "document" : "documents";

  // The sub-documents a needs_attention run could not summarize, keyed by page range for matching
  // to editor rows (the idx in attention is the included-position, not review_row.idx - match on
  // pages).
  const failedRows = wf.attention?.rows ?? [];
  const attentionPages = new Set(failedRows.map((r) => r.pages));
  const titleByPages = new Map<string, string>(
    wf.rows.map((r) => [
      `${r.start}-${r.end}`,
      r.title && r.title !== "-" ? r.title : "",
    ]),
  );

  // Surfaced on the tab so the size of the manual check the reviewers asked for is visible from
  // Duplicates and Summaries too, not only from the tab that carries the filter (issue #144).
  const unidentified = wf.rows.filter(couldNotIdentify).length;

  const tabs = [
    {
      value: "review" as const,
      label: unidentified
        ? `Review & correct · ${unidentified}`
        : "Review & correct",
    },
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
  const paused =
    wf.watching && wf.progress.detail.toLowerCase().startsWith("paused");

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

  // Reset on the JOB boundary, not only when watching ends. One reviewer action can run into a
  // second job - Summarize flushes rows and enqueues, and a chained or immediately-started job
  // changes the active id while `watching` stays true throughout - and keying
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
      forceTimer.current = setTimeout(
        () => setForceReady(true),
        graceSeconds * 1000,
      );
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
        (s) =>
          s.edited &&
          s.rowCategoryLive !== null &&
          s.rowCategoryLive !== s.row.category,
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
  // No CURRENT duplicate check covers these rows: either none has ever completed, or the documents
  // have moved since the last one. The server refuses summarize in that state (#125) and the button
  // has to say so rather than let the reviewer meet a 409. Undefined while the payload is still
  // loading, and an unloaded payload must not disable the button - `?? false` keeps it enabled.
  const needsDuplicateCheck = dupData
    ? !dupData.checked || dupData.stale
    : false;

  // Block Summarize while any row is invalid, nothing is selected, a save failed/is pending, a
  // duplicate check is running, or none has covered these rows - so a user never summarizes stale
  // or invalid rows, and never ships duplicates nobody has looked at.
  const summarizeDisabled =
    errors.size > 0 ||
    included === 0 ||
    save.kind === "error" ||
    save.kind === "dirty" ||
    dedupRunning ||
    needsDuplicateCheck;

  const checkDuplicatesHint = checkDuplicatesReason(save.kind, dedupRunning);
  const summarizeHint = summarizeReason({
    disabled: summarizeDisabled,
    errorCount: errors.size,
    included,
    dedupRunning,
    needsDuplicateCheck,
    hasChecked: Boolean(dupData?.checked),
  });

  // The gate is SOFT: a reviewer may have a good reason to skip on a short record. Skipping is a
  // decision, so it is a separate control behind a confirm, and the server audits it - an omission
  // that leaves no trace is the defect #125 exists to close.
  const onSummarizeWithoutChecking = () => {
    if (
      window.confirm(
        "Summarize without checking for duplicates? Duplicate copies of the same document " +
          "would be summarized and delivered without anyone seeing them. This choice is " +
          "recorded.",
      )
    ) {
      void wf.onSummarize(false, true);
    }
  };

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
        humanizeError(err, {
          fallback: "Could not start the check - please try again.",
        }),
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
      wf.setBanner(
        humanizeError(err, {
          fallback: "Could not start the check - please try again.",
        }),
      );
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
              {recordCountLabel(wf.rows.length, wf.totalPages)}
            </span>
          </div>
        </div>

        <SegmentedTabs
          tabs={tabs}
          value={tab}
          onValueChange={setTab}
          ariaLabel="Editor view"
        />

        <div className="rce-bar-actions">
          <RunningProgress
            watching={wf.watching}
            progress={wf.progress}
            paused={paused}
            stopping={stopping}
            forceReady={forceReady}
            onStop={onStop}
          />
          <SaveChip watching={wf.watching} tab={tab} save={save} />
          {/* Each tab carries its own step's actions: correct the documents, then clear the
              duplicates, then summarize - so the reviewer passes the duplicates gate. */}
          <ReviewStepActions
            watching={wf.watching}
            tab={tab}
            rowCount={wf.rows.length}
            dedupRunning={dedupRunning}
            recheckPending={recheck.isPending}
            saveKind={save.kind}
            checkDuplicatesHint={checkDuplicatesHint}
            onStart={wf.onStart}
            onCheckDuplicates={onCheckDuplicates}
          />
          <DuplicatesStepActions
            watching={wf.watching}
            tab={tab}
            recheckPending={recheck.isPending}
            dedupRunning={dedupRunning}
            summarizeDisabled={summarizeDisabled}
            summarizeHint={summarizeHint}
            included={included}
            documentNoun={documentNoun}
            needsDuplicateCheck={needsDuplicateCheck}
            errorCount={errors.size}
            saveKind={save.kind}
            onRecheck={onRecheck}
            onSummarize={() => wf.onSummarize()}
            onSummarizeWithoutChecking={onSummarizeWithoutChecking}
          />
          <SummariesStepActions
            watching={wf.watching}
            tab={tab}
            summariesCount={summaries.length}
            onReSummarizeAll={reSummarizeAll}
          />
        </div>
      </header>

      <ReviewBanners
        wf={wf}
        tab={tab}
        unresolvedDupes={unresolvedDupes}
        failedRows={failedRows}
        titleByPages={titleByPages}
        showSummarizeBlockers={showSummarizeBlockers}
        save={save}
        errors={errors}
        onRestart={onRestart}
        onReviewDuplicates={() => setTab("duplicates")}
      />

      <ReviewBody
        tab={tab}
        wf={wf}
        documentId={documentId}
        attentionPages={attentionPages}
        onGotoSummarizeStep={() => setTab("duplicates")}
      />
    </div>
  );
}
