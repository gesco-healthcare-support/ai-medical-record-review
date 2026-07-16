"use client";

import { useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CategoryOption, Row } from "@/lib/types";
import { newKey, rowErrors, type EditorRow } from "@/lib/review-rows";
import { RowsTable } from "./rows-table";
import { PdfViewer, type PdfViewerHandle } from "./pdf-viewer";

export type SaveState = { kind: "" | "saved" | "dirty" | "error"; message?: string };

function mergedFlag(a: string, b: string) {
  return [a, b].includes("x") ? "x" : "-";
}

/** Review & correct workbench (DS #step-editor): header + resizable split (rows table | PDF). */
export function ReviewEditor({
  documentId,
  filename,
  rows,
  categories,
  totalPages,
  saveState,
  onRowsChange,
  onSummarize,
  showSummarize = true,
}: {
  documentId: string;
  filename: string;
  rows: EditorRow[];
  categories: CategoryOption[];
  totalPages: number;
  saveState: SaveState;
  onRowsChange: (rows: EditorRow[]) => void;
  onSummarize?: () => void;
  showSummarize?: boolean;
}) {
  const pdfRef = useRef<PdfViewerHandle>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [splittingKey, setSplittingKey] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addStart, setAddStart] = useState("");
  const [addEnd, setAddEnd] = useState("");

  const errors = rowErrors(rows, totalPages);
  const selected = rows.findIndex((r) => r._key === selectedKey);
  const splitting = rows.findIndex((r) => r._key === splittingKey);
  const included = rows.filter((r) => r.include !== false).length;
  const suggested = rows.filter((r, i) => r.suggest_merge && i > 0).length;
  const firstError = errors.size
    ? `row ${[...errors.keys()][0] + 1}: ${[...errors.values()][0]}`
    : "";

  function select(i: number) {
    setSelectedKey(rows[i]?._key ?? null);
    pdfRef.current?.jumpTo(Number(rows[i]?.start) || 1);
  }

  function field(i: number, patch: Partial<Row>) {
    onRowsChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }

  function mergeUp(i: number) {
    if (i <= 0) return;
    const next = [...rows];
    next[i - 1] = {
      ...next[i - 1],
      end: Math.max(next[i - 1].end, next[i].end),
      flag: mergedFlag(next[i - 1].flag, next[i].flag),
    };
    setSelectedKey(next[i - 1]._key);
    next.splice(i, 1);
    setSplittingKey(null);
    onRowsChange(next);
  }

  function splitConfirm(i: number, k: number) {
    const row = rows[i];
    if (!Number.isInteger(k) || k <= Number(row.start) || k > Number(row.end)) return;
    const secondHalf: EditorRow = {
      start: k,
      end: Number(row.end),
      category: row.category,
      title: "-",
      date: row.date,
      injury_date: row.injury_date,
      flag: "x",
      suggest_merge: false,
      include: row.include !== false,
      _key: newKey(),
    };
    const next = rows.map((r, idx) => (idx === i ? { ...r, end: k - 1 } : r));
    next.splice(i + 1, 0, secondHalf);
    setSplittingKey(null);
    setSelectedKey(secondHalf._key);
    onRowsChange(next);
    pdfRef.current?.jumpTo(k);
  }

  function remove(i: number) {
    const next = [...rows];
    next.splice(i, 1);
    setSelectedKey(null);
    setSplittingKey(null);
    onRowsChange(next);
  }

  function applySuggestions() {
    const next = [...rows];
    for (let i = 1; i < next.length; ) {
      if (next[i].suggest_merge) {
        next[i - 1] = {
          ...next[i - 1],
          end: Math.max(next[i - 1].end, next[i].end),
          flag: mergedFlag(next[i - 1].flag, next[i].flag),
        };
        next.splice(i, 1);
      } else {
        i += 1;
      }
    }
    setSelectedKey(null);
    setSplittingKey(null);
    onRowsChange(next);
  }

  function openAdd() {
    const last = rows[rows.length - 1];
    const start = last ? Math.min(Number(last.end) + 1, totalPages) : 1;
    setAddStart(String(start));
    setAddEnd(String(start));
    setAddOpen(true);
  }

  function confirmAdd() {
    const start = Number(addStart);
    const end = Number(addEnd);
    const bad = (v: number) => !Number.isInteger(v) || v < 1 || v > totalPages;
    if (bad(start) || bad(end) || start > end) return;
    const inserted: EditorRow = {
      start,
      end,
      category: "100",
      title: "(added manually)",
      date: "-",
      injury_date: "-",
      flag: "x",
      suggest_merge: false,
      include: true,
      _key: newKey(),
    };
    setSelectedKey(inserted._key);
    setSplittingKey(null);
    setAddOpen(false);
    onRowsChange([...rows, inserted]);
    pdfRef.current?.jumpTo(start);
  }

  return (
    <section id="step-editor">
      <div className="rc-column">
        <div className="rc-filename">{filename}</div>
        <div className="rc-header">
          <div className="rc-status">
            <h1>
              {rows.length} documents / {totalPages} pages
            </h1>
            <span className={cn("rc-save", saveState.kind)}>
              {saveState.kind === "saved" ? (
                <>
                  <Check width={14} height={14} aria-hidden /> Saved
                </>
              ) : (
                saveState.message
              )}
            </span>
            <span className="error-text">{firstError}</span>
          </div>
          <div className="rc-actions">
            {suggested > 0 ? (
              <button type="button" className="ev-btn ev-btn-gold" onClick={applySuggestions}>
                Apply {suggested} suggested merge{suggested === 1 ? "" : "s"}
              </button>
            ) : null}
            {addOpen ? (
              <span className="add-form">
                pages{" "}
                <input
                  type="number"
                  min={1}
                  max={totalPages}
                  value={addStart}
                  aria-label="First page of the new document"
                  onChange={(e) => setAddStart(e.target.value)}
                />{" "}
                to{" "}
                <input
                  type="number"
                  min={1}
                  max={totalPages}
                  value={addEnd}
                  aria-label="Last page of the new document"
                  onChange={(e) => setAddEnd(e.target.value)}
                />
                <button
                  type="button"
                  className="ev-btn ev-btn-sm ev-btn-outline"
                  onClick={confirmAdd}
                >
                  Insert
                </button>
                <button
                  type="button"
                  className="ev-btn ev-btn-sm ev-btn-ghost"
                  onClick={() => setAddOpen(false)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button type="button" className="ev-btn ev-btn-outline" onClick={openAdd}>
                + Insert document
              </button>
            )}
            {showSummarize ? (
              <button
                type="button"
                className="ev-btn ev-btn-primary ev-btn-lg"
                disabled={errors.size > 0 || included === 0}
                onClick={onSummarize}
              >
                {included
                  ? `Summarize ${included} document${included === 1 ? "" : "s"}`
                  : "Summarize"}
              </button>
            ) : null}
          </div>
        </div>
        <div className="editor-split">
          <div className="table-wrap">
            <RowsTable
              rows={rows}
              categories={categories}
              totalPages={totalPages}
              errors={errors}
              selected={selected}
              splitting={splitting}
              onSelect={select}
              onField={field}
              onMergeUp={mergeUp}
              onSplitStart={(i) => setSplittingKey(rows[i]._key)}
              onSplitConfirm={splitConfirm}
              onSplitCancel={() => setSplittingKey(null)}
              onDelete={remove}
            />
          </div>
          <div className="viewer-wrap">
            <PdfViewer ref={pdfRef} documentId={documentId} />
          </div>
        </div>
      </div>
    </section>
  );
}
