"use client";

import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { Stepper } from "./stepper";
import { StartPanel } from "./start-panel";
import { ProgressPanel } from "./progress-panel";
import { ReviewEditor } from "./review-editor";
import { SummariesView } from "./summaries-view";

/** The /records/[id] screen: the shared identify/review/summaries workflow plus the 3-step
 *  stepper. All lifecycle state lives in useReviewWorkflow; this component just renders it. */
export function ReviewPageClient({ documentId }: { documentId: string }) {
  const wf = useReviewWorkflow(documentId);

  return (
    <>
      <Stepper activeStep={wf.activeStep} busy={wf.watching} onStep={wf.gotoStep} />
      {wf.banner ? <div className="banner">{wf.banner}</div> : null}
      <main>
        {wf.section === "start" ? (
          <StartPanel rerun={wf.rows.length > 0} hint={wf.startHint} onStart={wf.onStart} />
        ) : null}
        {wf.section === "progress" ? (
          <ProgressPanel title={wf.progress.title} pct={wf.progress.pct} detail={wf.progress.detail} />
        ) : null}
        {wf.section === "editor" ? (
          <ReviewEditor
            documentId={documentId}
            filename={wf.filename}
            rows={wf.rows}
            categories={wf.categories}
            totalPages={wf.totalPages}
            saveState={wf.saveState}
            onRowsChange={wf.onRowsChange}
            onSummarize={wf.onSummarize}
          />
        ) : null}
        {wf.section === "summaries" ? (
          <SummariesView
            documentId={documentId}
            categories={wf.categories}
            onGotoReview={() => wf.gotoStep("review")}
          />
        ) : null}
      </main>
    </>
  );
}
