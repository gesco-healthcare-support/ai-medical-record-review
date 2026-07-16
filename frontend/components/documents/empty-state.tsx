import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

/** First-run empty state (DS .hd-empty): dropzone + 3-step explainer. Drag-drop is handled by
 *  the parent (which wraps the whole area); this shows the visual dragging state and a browse CTA. */
export function EmptyState({
  dragging,
  uploading,
  onBrowse,
}: {
  dragging: boolean;
  uploading: boolean;
  onBrowse: () => void;
}) {
  return (
    <section className="hd-empty">
      <h1>Start your first review</h1>
      <p className="hd-empty-sub">
        Upload a medical record and MRR identifies the documents inside it. You review and correct
        the result before anything is summarized.
      </p>
      <div
        className={cn("hd-drop", dragging && "dragging")}
        role="button"
        tabIndex={0}
        aria-label="Drag a PDF here, or browse your files"
        onClick={onBrowse}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onBrowse();
          }
        }}
      >
        <span className="hd-drop-icon">
          <UploadCloud width={26} height={26} aria-hidden />
        </span>
        <div className="hd-drop-title">Drag a PDF here, or browse your files</div>
        <div className="hd-drop-sub">One record per upload</div>
        <button
          className="ev-btn ev-btn-outline"
          type="button"
          disabled={uploading}
          onClick={(e) => {
            e.stopPropagation();
            onBrowse();
          }}
        >
          {uploading ? "Uploading..." : "Browse files"}
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
