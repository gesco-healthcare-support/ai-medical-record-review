import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

/** First-run empty state (DS .hd-empty): dropzone + 3-step explainer. Drag-drop is handled by
 *  the parent (which wraps the whole area and opens the upload form with the files dropped); this
 *  shows the visual dragging state and the "Upload records" button. */
export function EmptyState({
  dragging,
  onUpload,
}: Readonly<{
  dragging: boolean;
  onUpload: () => void;
}>) {
  return (
    <section className="hd-empty">
      <h1>Start your first review</h1>
      <p className="hd-empty-sub">
        Upload a medical record and MRR identifies the documents inside it. You review and correct
        the result before anything is summarized.
      </p>
      {/* Where files are dropped. The drop itself is the page's (it opens the upload form with the
          files); the button is the one control, and opens the form empty. */}
      <div className={cn("hd-drop", dragging && "dragging")}>
        <span className="hd-drop-icon">
          <UploadCloud width={26} height={26} aria-hidden />
        </span>
        <div className="hd-drop-title">Drag the record&apos;s files here, or</div>
        <div className="hd-drop-sub">One PDF, or every file the records came in</div>
        <button className="ev-btn ev-btn-primary" type="button" onClick={onUpload}>
          Upload records
        </button>
      </div>
      <div className="hd-steps">
        <div className="hd-step">
          <span className="hd-step-num">1</span>
          <div className="hd-step-title">Upload the record</div>
          <div className="hd-step-sub">
            The PDF is split into its component documents and each one is categorized.
          </div>
        </div>
        <div className="hd-step">
          <span className="hd-step-num">2</span>
          <div className="hd-step-title">Review &amp; correct</div>
          <div className="hd-step-sub">
            Check page ranges, categories, and dates side by side with the PDF. Merge, split, or
            skip documents.
          </div>
        </div>
        <div className="hd-step">
          <span className="hd-step-num">3</span>
          <div className="hd-step-title">Export summaries</div>
          <div className="hd-step-sub">
            Each document is summarized; you edit or exclude, then export the review to Word.
          </div>
        </div>
      </div>
    </section>
  );
}
