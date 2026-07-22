"use client";

import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { rowErrors } from "@/lib/review-rows";
import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { useSummaries } from "@/hooks/use-summaries";
import { SegmentedTabs } from "@/components/ui/segmented-tabs";
import { BackLink } from "@/components/app/back-link";
import { ReviewEditor } from "./review-editor";
import { SummariesView } from "./summaries-view";
import { HeaderBar } from "./header-bar";
import { StartPanel } from "./start-panel";
import { ProgressPanel } from "./progress-panel";

type Tab = "review" | "summaries";

/** The /records/[id] workbench: one slim header (back, record name + count, SegmentedTabs, autosave,
 *  Auto-fill / Segment / Summarize) over a tab body - the always-on Review & correct editor or the
 *  Summaries view. The identify/summarize lifecycle lives in useReviewWorkflow; a running job turns
 *  the header actions into an inline progress bar and dims the editor. */
export function ReviewPageClient({ documentId }: { documentId: string }) {
  const wf = useReviewWorkflow(documentId);
  const { data: summaries = [] } = useSummaries(documentId);
  const [tab, setTab] = useState<Tab>("review");
  const lastSection = useRef(wf.section);

  // The hook lands on "summaries" after a summarize job finishes (or when a done record boots);
  // follow it to the Summaries tab, but leave manual tab switches alone afterward.
  useEffect(() => {
    if (wf.section === "summaries" && lastSection.current !== "summaries") setTab("summaries");
    lastSection.current = wf.section;
  }, [wf.section]);

  const errors = rowErrors(wf.rows, wf.totalPages);
  const included = wf.rows.filter((r) => r.include !== false).length;

  const tabs = [
    { value: "review" as const, label: "Review & correct" },
    {
      value: "summaries" as const,
      label: summaries.length ? `Summaries · ${summaries.length}` : "Summaries",
    },
  ];

  const save = wf.saveState;
  // The paused stage label is stable (STAGE_LABELS.paused); style the bar distinctly while waiting.
  const paused = wf.watching && wf.progress.detail.toLowerCase().startsWith("paused");
  // Block Summarize while any row is invalid, nothing is selected, or a save failed/is pending -
  // so a user never summarizes stale or invalid rows.
  const summarizeDisabled =
    errors.size > 0 || included === 0 || save.kind === "error" || save.kind === "dirty";

  const reSummarizeAll = () => {
    if (
      window.confirm(
        "Re-summarize all documents? This discards the current summaries and any edits to them.",
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
              <button type="button" className="ev-btn ev-btn-outline" onClick={wf.onStart}>
                {wf.rows.length ? "Re-run segment" : "Segment"}
              </button>
              {tab === "review" ? (
                <>
                  {summaries.length > 0 ? (
                    <button type="button" className="ev-btn ev-btn-ghost" onClick={reSummarizeAll}>
                      Re-summarize all
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="ev-btn ev-btn-primary"
                    disabled={summarizeDisabled}
                    title={
                      !summarizeDisabled
                        ? undefined
                        : errors.size > 0
                          ? "Fix the highlighted page ranges before summarizing."
                          : included === 0
                            ? "Select at least one document to summarize."
                            : "Your latest changes aren't saved yet."
                    }
                    onClick={() => wf.onSummarize()}
                  >
                    {included
                      ? `Summarize ${included} document${included === 1 ? "" : "s"}`
                      : "Summarize"}
                  </button>
                </>
              ) : null}
            </>
          )}
        </div>
      </header>

      {wf.banner ? <div className="banner">{wf.banner}</div> : null}
      {wf.attention ? (
        <div className="notice-attention" role="status">
          {wf.attention.message}
        </div>
      ) : null}
      {tab === "review" && save.kind === "error" && errors.size === 0 ? (
        <div className="banner" role="alert">
          {save.message}
        </div>
      ) : null}
      {tab === "review" && errors.size > 0 ? (
        <div className="banner" role="status">
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
                />
              </div>
            </>
          )
        ) : (
          <SummariesView
            documentId={documentId}
            categories={wf.categories}
            header={wf.header}
            onGotoReview={() => setTab("review")}
          />
        )}
      </div>
    </div>
  );
}
