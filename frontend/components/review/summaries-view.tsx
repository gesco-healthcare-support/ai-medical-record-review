"use client";

import { useState } from "react";
import { FileText, Flag, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useResummarize, useSaveSummary, useSummaries } from "@/hooks/use-summaries";
import type { CategoryOption, SummaryItem } from "@/lib/types";
import type { HeaderFields } from "@/lib/review-api";
import { ExportDialog } from "./export-dialog";
import { HeaderBar } from "./header-bar";
import { MarkdownText } from "./markdown-text";

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
 *  Excluded badges, inline edit, Re-draft, and an "In export" toggle. The same editable report
 *  header as Review & correct sits on top (shared via onHeaderSaved), and the Export dialog
 *  prefills from it. */
export function SummariesView({
  documentId,
  categories,
  header,
  onHeaderSaved,
  onGotoReview,
}: {
  documentId: string;
  categories: CategoryOption[];
  header?: HeaderFields | null;
  onHeaderSaved?: (fields: HeaderFields) => void;
  onGotoReview: () => void;
}) {
  const { data: summaries = [], isLoading, error } = useSummaries(documentId);
  const save = useSaveSummary(documentId);
  const redraft = useResummarize(documentId);

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

  return (
    <section id="step-summaries">
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
            Export to Word
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
                <div
                  key={item.idx}
                  className={cn(
                    "summary-card",
                    item.excluded && "excluded",
                    redraftingIdx === item.idx && "busy",
                  )}
                >
                  <div className="summary-head">
                    <h3 className="sum-heading">
                      <MarkdownText text={title} />
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
                    {item.excluded ? <span className="ev-chip ev-chip-neutral">Excluded</span> : null}
                    <span className="card-actions">
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost ev-btn-sm"
                        disabled={redraftingIdx === item.idx}
                        onClick={() => reDraft(item)}
                      >
                        {redraftingIdx === item.idx ? "Re-drafting..." : "Re-draft"}
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost ev-btn-sm"
                        onClick={() => startEdit(item)}
                      >
                        Edit
                      </button>
                      <label className="exclude-toggle">
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
