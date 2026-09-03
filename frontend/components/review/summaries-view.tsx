"use client";

import { useRef, useState } from "react";
import { FileText, Flag, Pencil, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useResummarize, useSaveSummary, useSummaries } from "@/hooks/use-summaries";
import { categoryOptions } from "@/components/review/rows-table";
import { categoryWasGuessed } from "@/lib/review-rows";
import type { CategoryOption, SummaryItem } from "@/lib/types";
import type { HeaderFields } from "@/lib/review-api";
import { ExportDialog } from "./export-dialog";
import { HeaderBar } from "./header-bar";
import { MarkdownText } from "./markdown-text";
import { PdfViewer, type PdfViewerHandle } from "./pdf-viewer";
import { SplitPane } from "./split-pane";

const PAGE_SIZE = 20;

// Mirrors summary_doi.py. TWO grammars are read, because summaries stored before 2026-07-29 carry
// the older one and must keep showing their DOI in the chip instead of leaving a raw "**DOI**:..."
// in the body:
//   house  "**DOI**: 05/08/22 & 06/01/23."  /  "**DOI**: CT 01/02/20-03/04/21."
//   legacy "**DOI**:05/08/2022, 06/01/2023,"
// Every date must be matched in both - stopping at the first separator would hide a second injury
// date the document actually stated.
// Two additions, mirroring services/summary_doi.py, and both are about not dropping a stated date.
// `CT\s*` could not consume the colon in "CT:", and items could only be joined by "&" - but the
// multi-DOI shape both injury_date columns document is "MM/DD/YYYY, MM/DD/YYYY", and the review
// page's injury-date cell is free text stored verbatim. A comma-joined prefix therefore failed NEW,
// fell through to LEGACY, which requires a trailing comma and so backtracks to the FIRST one: the
// chip showed one date and the second was left as the opening words of the body.
const DOI_DATE = String.raw`\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}`;
const DOI_ITEM = String.raw`(?:\bC\.?T\.?\s*:?\s*)?${DOI_DATE}(?:\s*-\s*${DOI_DATE})?`;
const DOI_PREFIX_NEW = new RegExp(
  String.raw`^\s*\*\*DOI\*\*:\s*(${DOI_ITEM}(?:\s*[&,]\s*${DOI_ITEM})*)\s*\.\s*`,
  "i",
);
const DOI_PREFIX_LEGACY = /^\s*\*\*DOI\*\*:\s*([\d/.-]{4,}(?:\s*,\s*[\d/.-]{4,})*)\s*,\s*/;

const MANUAL_CHECK_PREFIX = /^\s*\[ManualCheck\]\s*/i;
const PAGES_SUFFIX = /\(Pages\s+\d+\s*[-–]\s*\d+\)\s*$/i;
const DIAGNOSTIC_SUFFIX = /\[Diagnostic Study\]\s*$/i;

/** Drop a trailing marker and the whitespace in front of it.
 *
 *  The two suffix patterns deliberately do NOT begin with `\s*`. Written as `\s*MARKER…$` the engine
 *  re-scans the whitespace run from every start position, which is quadratic: on a whitespace-only
 *  title of 20,000 characters that measured 424ms against 0.07ms for this form. Titles come out of
 *  the model, so a pathological one would stall the tab. Matching the marker first and trimming what
 *  preceded it is linear and yields the identical string. */
function stripTrailingMarker(value: string, marker: RegExp) {
  const found = marker.exec(value);
  return found ? value.slice(0, found.index).trimEnd() : value;
}

/** The title as the reading column shows it, with the decorations the engine bakes into the stored
 *  string removed. Exported for the equivalence + timing test; the view goes through parseDisplay. */
export function displayTitle(raw: string) {
  const body = (raw || "").replace(MANUAL_CHECK_PREFIX, "");
  return stripTrailingMarker(stripTrailingMarker(body, PAGES_SUFFIX), DIAGNOSTIC_SUFFIX);
}

/** Strip the decorations the engine bakes into stored strings; the web view shows chips/meta. */
function parseDisplay(item: SummaryItem) {
  const title = displayTitle(item.summaryTitle || "");
  let text = item.summaryText || "";
  let doi: string | null = null;
  const match = DOI_PREFIX_NEW.exec(text) ?? DOI_PREFIX_LEGACY.exec(text);
  if (match) {
    doi = match[1].trim();
    text = text.slice(match[0].length);
  }
  return { title, text, doi };
}

/** True when the row was re-classified after this summary was written.
 *
 *  `??` rather than a `!== null` guard, and deliberately: null means no row covers these pages any
 *  more, and UNDEFINED means the field is missing entirely because an older backend is serving this
 *  page. Neither is a mismatch, but `undefined !== null` is true, so the explicit null check reported
 *  every card as stale during a rolling deploy. Coalescing to the snapshot makes both absences
 *  compare equal and stay silent.
 *
 *  Module scope, not inside the component: it closes over nothing, so re-creating it on
 *  every render bought nothing. */
function categoryIsStale(item: SummaryItem) {
  return (item.rowCategoryLive ?? item.row.category) !== item.row.category;
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
  onGotoSummarizeStep,
  onRowsChanged,
}: Readonly<{
  documentId: string;
  filename?: string;
  categories: CategoryOption[];
  header?: HeaderFields | null;
  onHeaderSaved?: (fields: HeaderFields) => void;
  /** Opens the step that owns the Summarize button, for the empty state to send the reviewer there. */
  onGotoSummarizeStep: () => void;
  /** Re-pull the editor's rows after a category change wrote to one server-side. NOT optional in
   *  practice: the Review & correct tab renders from an in-memory buffer and autosaves the WHOLE set,
   *  so leaving it stale means the reviewer's next edit there silently reverts the category. Same
   *  reason DuplicatesView takes onResolved. */
  onRowsChanged?: () => void;
}>) {
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

  /** Re-classify this sub-document. The server writes it to the owning row, so the change is shared
   *  with Review & correct; the summary TEXT is untouched until the reviewer re-drafts. */
  async function changeCategory(idx: number, category: string) {
    setSaveMsg("Saving category...");
    try {
      await save.mutateAsync({ idx, body: { category } });
      // The write landed on a ReviewRow, so the editor's buffer is now stale. It autosaves the whole
      // set, so without this the reviewer's next edit on Review & correct sends the OLD category back
      // and undoes this change with no error anywhere.
      onRowsChanged?.();
      setSaveMsg("Category saved - re-draft to apply it to the summary");
    } catch (err) {
      // A 409 here means a job is running: the row would be overwritten, so the server refused.
      setSaveMsg(`Category not saved: ${humanizeError(err, { fallback: "please try again" })}`);
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
  const summaryNoun = summaries.length === 1 ? "summary" : "summaries";
  const excludedNote = excludedCount ? ` · ${excludedCount} excluded from export` : "";
  const countLine = summaries.length
    ? `${summaries.length} ${summaryNoun}` + excludedNote
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

        {!isLoading && summaries.length === 0 ? (
          <div className="summary-empty">
            <FileText width={34} height={34} aria-hidden />
            <p className="empty-title">No summaries yet</p>
            <p>Summaries appear here once you run summarization from the Duplicates step.</p>
            <button type="button" className="ev-btn ev-btn-primary" onClick={onGotoSummarizeStep}>
              Go to Duplicates
            </button>
          </div>
        ) : null}
        {!isLoading && summaries.length > 0 ? (
          <div className="summary-list">
            {pageItems.map((item) => {
              const { title, text, doi } = parseDisplay(item);
              // No category here any more: the select beside this line owns that value. Printing the
              // generating snapshot as well would put two different category values on one card with
              // nothing to explain the difference.
              const meta = [
                item.summaryDate || "no date",
                `pages ${item.row.start}–${item.row.end}`,
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
                // The title and the meta line are BUTTONS that show the summary's source pages: a
                // click handler on the card itself would be mouse-only (no keyboard path), and
                // making the card the control would nest Re-draft / Edit inside another control.
                <div
                  key={item.idx}
                  className={cn(
                    "summary-card",
                    item.excluded && "excluded",
                    redraftingIdx === item.idx && "busy",
                    selectedIdx === item.idx && "selected",
                  )}
                >
                  <div className="summary-head">
                    <h3 className="sum-heading">
                      <button type="button" className="row-jump" onClick={() => openSummary(item)}>
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
                    {categoryWasGuessed({
                      category: item.rowCategoryLive ?? item.row.category,
                      method: item.rowMethodLive,
                    }) ? (
                      <span
                        className="ev-chip ev-chip-review"
                        title="The category was a guess: no rule matched and the two classifiers did not agree. This summary was written under it, so check it against the pages before exporting."
                      >
                        <Flag width={12} height={12} aria-hidden />
                        Category guessed
                      </span>
                    ) : null}
                    {item.rowMissing ? (
                      <span
                        className="ev-chip ev-chip-review"
                        title="No sub-document covers these pages any more - they were merged or re-spanned after this summary was written. It still exports as-is. Re-run Summarize to rebuild it from the current sub-documents."
                      >
                        <Flag width={12} height={12} aria-hidden />
                        Pages changed - re-summarize
                      </span>
                    ) : null}
                    {categoryIsStale(item) ? (
                      <span
                        className="ev-chip ev-chip-review"
                        title={`This summary was written as ${categoryLabel(item.row.category)}. Re-draft it to rewrite under ${categoryLabel(item.rowCategoryLive as string)}.`}
                      >
                        <Flag width={12} height={12} aria-hidden />
                        Category changed - re-draft to apply
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
                  <div className="sum-meta-row">
                    <button type="button" className="row-jump meta-jump" onClick={() => openSummary(item)}>
                      <span className="meta">{meta}</span>
                    </button>
                    {/* The select shows the row's CURRENT classification, which is what the reviewer
                        acts on; the category that generated the text is named in the badge's tooltip
                        when the two differ. Outside the meta button on purpose - a select nested in a
                        button is invalid, and a click on it must not jump the viewer. */}
                    <label className="sum-category">
                      <span className="sum-category-label">Category</span>
                      <select
                        className="rc-sel"
                        aria-label="Document category"
                        value={item.rowCategoryLive ?? item.row.category}
                        disabled={redraftingIdx === item.idx}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => changeCategory(item.idx, e.target.value)}
                      >
                        {categoryOptions(categories, item.rowCategoryLive ?? item.row.category)}
                      </select>
                    </label>
                  </div>
                  <p className="body">
                    <MarkdownText text={text} />
                  </p>
                </div>
              );
            })}
          </div>
        ) : null}

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
