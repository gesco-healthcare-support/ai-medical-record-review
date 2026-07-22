import { cn } from "@/lib/utils";
import type { DocumentListItem } from "@/lib/types";

// Labels + tones ported verbatim from doc-table.js (the DS documents table).
const STATUS_LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  segmenting: "Identifying documents",
  reviewing: "Ready for review",
  summarizing: "Summarizing",
  done: "Summarized",
  needs_attention: "Needs attention",
  error: "Failed",
  interrupted: "Interrupted",
};
const STATUS_TONES: Record<string, string> = {
  uploaded: "neutral",
  segmenting: "info",
  reviewing: "warning",
  summarizing: "info",
  done: "success",
  needs_attention: "warning",
  error: "danger",
  interrupted: "danger",
};

/** DS status badge (.hd-badge): a colored pill with a dot; running states append "(current/total)". */
export function StatusPill({ doc }: { doc: DocumentListItem }) {
  const job = doc.active_job;
  const progress = job && job.total ? ` (${job.current}/${job.total})` : "";
  const label = (STATUS_LABELS[doc.status] ?? doc.status) + progress;
  const tone = STATUS_TONES[doc.status] ?? "neutral";
  return (
    <span className={cn("hd-badge", `hd-badge-${tone}`)}>
      <span className="hd-dot" aria-hidden />
      {label}
    </span>
  );
}
