import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

/** Id of the hidden file input in DocumentsView. Declared HERE and imported there, not the other
 *  way round: that file already imports this one, so the reverse would be a cycle. */
export const UPLOAD_INPUT_ID = "record-file-input";

/** First-run empty state (DS .hd-empty): dropzone + 3-step explainer. Drag-drop is handled by
 *  the parent (which wraps the whole area); this shows the visual dragging state and a browse CTA. */
export function EmptyState({
  dragging,
  uploading,
  onBrowse,
}: Readonly<{
  dragging: boolean;
  uploading: boolean;
  onBrowse: () => void;
}>) {
  return (
    <section className="hd-empty">
      <h1>Start your first review</h1>
      <p className="hd-empty-sub">
        Upload a medical record and MRR identifies the documents inside it. You review and correct
        the result before anything is summarized.
      </p>
      {/* A real <label> for the hidden file input, so the BROWSER opens the picker: clicking anywhere
          in the area still works, with no click handler on a non-interactive element and no role or
          tab stop of its own to duplicate the button below. Per the HTML spec a label is not
          activated by clicks on its interactive content, so the "Browse files" button does not fire
          this as well - it keeps its own handler and stays the one keyboard control. */}
      <label
        htmlFor={UPLOAD_INPUT_ID}
        className={cn("hd-drop", dragging && "dragging")}
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
      </label>
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
