"use client";

import { useRef, useState } from "react";
import { FileText, Flag, Pencil, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useResummarize, useSaveSummary, useSummaries } from "@/hooks/use-summaries";
import type { CategoryOption, SummaryItem } from "@/lib/types";
import type { HeaderFields } from "@/lib/review-api";
import { ExportDialog } from "./export-dialog";
import { HeaderBar } from "./header-bar";
import { MarkdownText } from "./markdown-text";
import { PdfViewer, type PdfViewerHandle } from "./pdf-viewer";
import { SplitPane } from "./split-pane";

const PAGE_SIZE = 20;

/** Strip the decorations the engine bakes into stored strings; the web view shows chips/meta. */
function parseDisplay(item: SummaryItem) {
  const title = (item.summaryTitle || "")
    .replace(/^\s*\[ManualCheck\]\s*/i, "")
    .replace(/\s*\(Pages\s+\d+\s*[-–]\s*\d+\)\s*$/i, "")
    .replace(/\s*\[Diagnostic Study\]\s*$/i, "");
  let text = item.summaryText || "";
  let doi: string | null = null;
  const match = text.match(/^\s*\*\*DOI\*\*:\s*([^,]*),?\s*/);
  if (match) {
    doi = match[1].trim();
    text = text.slice(match[0].length);
  }
  return { title, text, doi };
}

/** Summaries & export (DS §4): a reading column of SummaryCards with Edited / Manual check /
 *  Excluded badges, inline edit, Re-draft, and an "In export" toggle, beside the same PDF viewer as
 *  Review & correct - clicking a card jumps the viewer to that summary's first source page so the
 *  reviewer can check it against the record. The same editable report header as Review & correct sits
 *  on top (shared via onHeaderSaved), and the Export dialog prefills from it. */
export function SummariesView({
  documentId,
  filename,
  categories,
  header,
  onHeaderSaved,
  onGotoReview,
}: {
  documentId: string;
  filename?: string;
  categories: CategoryOption[];
  header?: HeaderFields | null;
  onHeaderSaved?: (fields: HeaderFields) => void;
  onGotoReview: () => void;
}) {
  const { data: summaries = [], isLoading, error } = useSummaries(documentId);
  const save = useSaveSummary(documentId);
  const redraft = useResummarize(documentId);

  const pdfRef = useRef<PdfViewerHandle>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [editingIdx, setEditingIdx] = useState(-1);
  const [saveMsg, setSaveMsg] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  // Edit buffers (one card edits at a time).
  const [editTitle, setEditTitle] = useState("");
  const [editDate, setEditDate] = useState("");
  const [editText, setEditText] = useState("");

  const redraftingIdx = redraft.isPending ? (redraft.variables ?? -1) : -1;
  const loadError = error ? humanizeError(error, { fallback: "Could not load summaries." }) : "";

  function categoryLabel(id: string) {
    const found = categories.find((c) => String(c.id) === String(id));
    return found ? `${found.id} - ${found.name}` : String(id);
  }

  /** Show this summary's source pages in the PDF pane (its first page). */
  function openSummary(item: SummaryItem) {
    setSelectedIdx(item.idx);
    pdfRef.current?.jumpTo(Number(item.row.start) || 1);
  }

  function startEdit(item: SummaryItem) {
    const { title, text } = parseDisplay(item);
    setEditTitle(title);
    setEditDate(item.summaryDate || "");
    setEditText(text);
    setEditingIdx(item.idx);
  }

  async function saveEdit(idx: number) {
    setSaveMsg("Saving...");
    try {
      await save.mutateAsync({
        idx,
        body: { summaryTitle: editTitle, summaryDate: editDate, summaryText: editText },
      });
      setEditingIdx(-1);
      setSaveMsg("Saved");
    } catch (err) {
      setSaveMsg(`Not saved: ${humanizeError(err, { fallback: "please try again" })}`);
    }
  }

  async function toggleInExport(idx: number, inExport: boolean) {
    try {
      await save.mutateAsync({ idx, body: { excluded: !inExport } });
    } catch (err) {
      setSaveMsg(`Not saved: ${humanizeError(err, { fallback: "please try again" })}`);
    }
  }

  async function reDraft(item: SummaryItem) {
    if (
      item.edited &&
      !window.confirm(
        "Re-drafting replaces this summary with fresh AI output and discards your edits to it. Continue?",
      )
    ) {
      return;
    }
    setSaveMsg("Re-drafting this summary...");
    try {
      await redraft.mutateAsync(item.idx);
      setSaveMsg("Re-drafted");
    } catch (err) {
      setSaveMsg(`Re-draft failed: ${humanizeError(err, { fallback: "please try again" })}`);
    }
  }

  const excludedCount = summaries.filter((s) => s.excluded).length;
  const includedCount = summaries.length - excludedCount;
  const countLine = summaries.length
    ? `${summaries.length} summar${summaries.length === 1 ? "y" : "ies"}` +
      (excludedCount ? ` · ${excludedCount} excluded from export` : "")
    : "";
  const pageCount = Math.max(1, Math.ceil(summaries.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount - 1);
  const pageItems = summaries.slice(curPage * PAGE_SIZE, curPage * PAGE_SIZE + PAGE_SIZE);

  const column = (
    <div className="rce-splitcol">
      <div className="sum-column">
        <HeaderBar
          documentId={documentId}
          header={header ?? null}
          onSaved={(f) => onHeaderSaved?.(f)}
        />
        <div className="sum-header">
          <div>
            <h1>Summaries</h1>
            <div className="sum-countline">
              <span>{countLine}</span>
              <span className="muted">{saveMsg || loadError}</span>
            </div>
          </div>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            disabled={summaries.length === 0 || includedCount === 0}
            onClick={() => setExportOpen(true)}
          >
            Export
          </button>
        </div>

        {isLoading ? null : summaries.length === 0 ? (
          <div className="summary-empty">
            <FileText width={34} height={34} aria-hidden />
            <p className="empty-title">No summaries yet</p>
            <p>Summaries appear here after you run summarization from Review &amp; correct.</p>
            <button type="button" className="ev-btn ev-btn-primary" onClick={onGotoReview}>
              Go to Review &amp; correct
            </button>
          </div>
        ) : (
          <div className="summary-list">
            {pageItems.map((item) => {
              const { title, text, doi } = parseDisplay(item);
              const meta = [
                item.summaryDate || "no date",
                `pages ${item.row.start}–${item.row.end}`,
                categoryLabel(item.row.category),
                doi ? `DOI ${doi}` : "",
              ]
                .filter(Boolean)
                .join(" · ");

              if (editingIdx === item.idx) {
                return (
                  <div key={item.idx} className="summary-card editing">
                    <div className="summary-head">
                      <input
                        className="ev-inp sum-title"
                        aria-label="Summary title"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                      />
                      <input
                        className="ev-inp sum-date"
                        aria-label="Summary date"
                        value={editDate}
                        onChange={(e) => setEditDate(e.target.value)}
                      />
                    </div>
                    <div className="meta">{meta}</div>
                    <textarea
                      className="ev-inp sum-text"
                      aria-label="Summary text"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                    />
                    <div className="edit-actions">
                      <button
                        type="button"
                        className="ev-btn ev-btn-primary"
                        disabled={save.isPending}
                        onClick={() => saveEdit(item.idx)}
                      >
                        {save.isPending ? "Saving..." : "Save"}
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost"
                        onClick={() => setEditingIdx(-1)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                );
              }

              return (
                // Clicking the card shows its source pages; the card is a plain div (not a button)
                // so the actions inside stay valid, and the title button is the keyboard path.
                <div
                  key={item.idx}
                  className={cn(
                    "summary-card",
                    item.excluded && "excluded",
                    redraftingIdx === item.idx && "busy",
                    selectedIdx === item.idx && "selected",
                  )}
                  onClick={() => openSummary(item)}
                >
                  <div className="summary-head">
                    <h3 className="sum-heading">
                      <button
                        type="button"
                        className="row-jump"
                        onClick={(e) => {
                          e.stopPropagation();
                          openSummary(item);
                        }}
                      >
                        <MarkdownText text={title} />
                      </button>
                    </h3>
                    {item.edited ? (
                      <span className="ev-chip ev-chip-edit">
                        <Pencil width={12} height={12} aria-hidden />
                        Edited
                      </span>
                    ) : null}
                    {item.manualCheck ? (
                      <span className="ev-chip ev-chip-review">
                        <Flag width={12} height={12} aria-hidden />
                        Manual check
                      </span>
                    ) : null}
                    {item.verifyChanged ? (
                      <span className="ev-chip ev-chip-review" title="AI verify pass corrected this summary - please confirm">
                        <ShieldCheck width={12} height={12} aria-hidden />
                        AI-fixed
                      </span>
                    ) : null}
                    {item.excluded ? <span className="ev-chip ev-chip-neutral">Excluded</span> : null}
                    <span className="card-actions">
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost ev-btn-sm"
                        disabled={redraftingIdx === item.idx}
                        onClick={(e) => {
                          e.stopPropagation();
                          reDraft(item);
                        }}
                      >
                        {redraftingIdx === item.idx ? "Re-drafting..." : "Re-draft"}
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost ev-btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          startEdit(item);
                        }}
                      >
                        Edit
                      </button>
                      <label className="exclude-toggle" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="ev-cb"
                          checked={!item.excluded}
                          onChange={(e) => toggleInExport(item.idx, e.target.checked)}
                        />{" "}
                        In export
                      </label>
                    </span>
                  </div>
                  <div className="meta">{meta}</div>
                  <p className="body">
                    <MarkdownText text={text} />
                  </p>
                </div>
              );
            })}
          </div>
        )}

        {pageCount > 1 ? (
          <div className="ev-pager">
            <button
              type="button"
              className="ev-btn ev-btn-outline ev-btn-sm"
              disabled={curPage === 0}
              onClick={() => {
                setPage((p) => Math.max(0, p - 1));
                setEditingIdx(-1);
              }}
            >
              Prev
            </button>
            <span>
              Page {curPage + 1} of {pageCount} · {curPage * PAGE_SIZE + 1}
              {"–"}
              {Math.min((curPage + 1) * PAGE_SIZE, summaries.length)} of {summaries.length}
            </span>
            <button
              type="button"
              className="ev-btn ev-btn-outline ev-btn-sm"
              disabled={curPage >= pageCount - 1}
              onClick={() => {
                setPage((p) => p + 1);
                setEditingIdx(-1);
              }}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );

  return (
    <section id="step-summaries" className="rce-split">
      <SplitPane
        storageKey="mrr.summaries.split"
        left={column}
        right={
          <div className="rce-viewer">
            <PdfViewer ref={pdfRef} documentId={documentId} filename={filename} />
          </div>
        }
      />

      <ExportDialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        documentId={documentId}
        includedCount={includedCount}
        excludedCount={excludedCount}
        defaults={header}
      />
    </section>
  );
}
