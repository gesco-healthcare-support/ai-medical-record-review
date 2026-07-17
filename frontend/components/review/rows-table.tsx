"use client";

import { Fragment, useRef, type MouseEvent } from "react";
import { ArrowUpToLine, Scissors, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CategoryOption, Row } from "@/lib/types";
import type { EditorRow } from "@/lib/review-rows";

function categoryOptions(categories: CategoryOption[], current: string) {
  const has = categories.some((c) => String(c.id) === String(current));
  const opts = has ? categories : [{ id: String(current), name: String(current) }, ...categories];
  return opts.map((c) => (
    <option key={c.id} value={c.id}>
      {c.id} - {c.name}
    </option>
  ));
}

const stop = (e: MouseEvent) => e.stopPropagation();

/**
 * The sub-documents table (DS #rowsTable). One row per document: page range, category, title,
 * date, injury date, review/summarize checkboxes, and hover row-tools (merge-up / split / delete).
 * A gold suggestion strip sits above a row the AI thinks continues the one above; gap strips mark
 * skipped pages; a split form drops in below the row being split. Purely presentational - every
 * edit is emitted via a callback and the parent owns the rows + autosave.
 */
export function RowsTable({
  rows,
  categories,
  totalPages,
  errors,
  selected,
  splitting,
  onSelect,
  onField,
  onMergeUp,
  onSplitStart,
  onSplitConfirm,
  onSplitCancel,
  onDelete,
}: {
  rows: EditorRow[];
  categories: CategoryOption[];
  totalPages: number;
  errors: Map<number, string>;
  selected: number;
  splitting: number;
  onSelect: (i: number) => void;
  onField: (i: number, patch: Partial<Row>) => void;
  onMergeUp: (i: number) => void;
  onSplitStart: (i: number) => void;
  onSplitConfirm: (i: number, atPage: number) => void;
  onSplitCancel: () => void;
  onDelete: (i: number) => void;
}) {
  const splitRef = useRef<HTMLInputElement>(null);
  let previousEnd = 0;

  return (
    <table id="rowsTable">
      <thead>
        <tr>
          <th className="rc-c-pages">Pages</th>
          <th className="rc-c-cat">Category</th>
          <th className="rc-c-title">Title</th>
          <th className="rc-c-date">Date</th>
          <th className="rc-c-injury">Injury date</th>
          <th className="rc-c-check" title="Flag for manual review">
            Review
          </th>
          <th className="rc-c-check rc-c-sum" title="Include this document in summarization">
            Summarize
          </th>
          <th className="rc-c-tools" aria-label="Row tools" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const included = row.include !== false;
          const showGap = Number(row.start) > previousEnd + 1;
          const gapFrom = previousEnd + 1;
          const gapTo = Number(row.start) - 1;
          previousEnd = Math.max(previousEnd, Number(row.end) || previousEnd);
          const titleValue = row.title && row.title !== "-" ? row.title : "";
          const injuryValue = row.injury_date && row.injury_date !== "-" ? row.injury_date : "";
          const dateValue = row.date && row.date !== "-" ? row.date : "";

          return (
            <Fragment key={row._key}>
              {showGap ? (
                <tr className="gap-row">
                  <td colSpan={8}>
                    pages {gapFrom}-{gapTo} not included (skipped at summarization)
                  </td>
                </tr>
              ) : null}

              {row.suggest_merge && i > 0 ? (
                <tr className="rc-suggest-row">
                  <td colSpan={8}>
                    <div className="rc-suggest">
                      <span>Likely continues the document above</span>
                      <button
                        type="button"
                        className="ev-btn ev-btn-sm ev-btn-gold"
                        onClick={(e) => {
                          stop(e);
                          onMergeUp(i);
                        }}
                      >
                        Merge
                      </button>
                    </div>
                  </td>
                </tr>
              ) : null}

              <tr
                className={cn(
                  "doc-row",
                  errors.has(i) && "invalid",
                  selected === i && "selected",
                  !included && "skipped",
                )}
                onClick={() => onSelect(i)}
              >
                <td className="rc-c-pages">
                  <div className="rc-pages">
                    <input
                      type="number"
                      className="rc-inp rc-pagenum"
                      value={row.start}
                      min={1}
                      max={totalPages}
                      aria-label="First page"
                      onClick={stop}
                      onChange={(e) => onField(i, { start: Number(e.target.value) })}
                    />
                    <span className="rc-dash" aria-hidden>
                      {"–"}
                    </span>
                    <input
                      type="number"
                      className="rc-inp rc-pagenum"
                      value={row.end}
                      min={1}
                      max={totalPages}
                      aria-label="Last page"
                      onClick={stop}
                      onChange={(e) => onField(i, { end: Number(e.target.value) })}
                    />
                  </div>
                </td>
                <td className="rc-c-cat">
                  <span className="rc-selwrap">
                    <select
                      className="rc-sel"
                      value={row.category}
                      aria-label="Category"
                      onClick={stop}
                      onChange={(e) => onField(i, { category: e.target.value })}
                    >
                      {categoryOptions(categories, row.category)}
                    </select>
                  </span>
                </td>
                <td className="rc-c-title">
                  <input
                    type="text"
                    className="rc-title"
                    placeholder="(untitled document)"
                    aria-label="Document title"
                    value={titleValue}
                    onClick={stop}
                    onChange={(e) => onField(i, { title: e.target.value })}
                  />
                </td>
                <td className="rc-c-date">
                  <input
                    type="text"
                    className="rc-inp"
                    value={dateValue}
                    aria-label="Document date"
                    onClick={stop}
                    onChange={(e) => onField(i, { date: e.target.value })}
                  />
                </td>
                <td className="rc-c-injury">
                  <input
                    type="text"
                    className="rc-inp"
                    value={injuryValue}
                    aria-label="Injury date"
                    onClick={stop}
                    onChange={(e) => onField(i, { injury_date: e.target.value })}
                  />
                </td>
                <td className="rc-c-check">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    aria-label="Flag for manual review"
                    checked={String(row.flag).toLowerCase() === "x"}
                    onClick={stop}
                    onChange={(e) => onField(i, { flag: e.target.checked ? "x" : "-" })}
                  />
                </td>
                <td className="rc-c-check rc-c-sum">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    aria-label="Include in summarization"
                    checked={included}
                    onClick={stop}
                    onChange={(e) => onField(i, { include: e.target.checked })}
                  />
                </td>
                <td className="rc-c-tools">
                  <span className="rc-rowtools">
                    <button
                      type="button"
                      className="rc-iconbtn"
                      disabled={i === 0}
                      title="Merge into the document above"
                      aria-label="Merge up"
                      onClick={(e) => {
                        stop(e);
                        onMergeUp(i);
                      }}
                    >
                      <ArrowUpToLine width={16} height={16} aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rc-iconbtn"
                      disabled={Number(row.end) <= Number(row.start)}
                      title="Split this document into two"
                      aria-label="Split"
                      onClick={(e) => {
                        stop(e);
                        onSplitStart(i);
                      }}
                    >
                      <Scissors width={16} height={16} aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="rc-iconbtn danger"
                      title="Remove this row"
                      aria-label="Delete"
                      onClick={(e) => {
                        stop(e);
                        onDelete(i);
                      }}
                    >
                      <Trash2 width={16} height={16} aria-hidden />
                    </button>
                  </span>
                </td>
              </tr>

              {splitting === i ? (
                <tr className="rc-splitrow">
                  <td colSpan={8}>
                    <span className="rc-splitform">
                      Split at page
                      <input
                        ref={splitRef}
                        type="number"
                        className="split-page"
                        min={Number(row.start) + 1}
                        max={row.end}
                        defaultValue={Number(row.start) + 1}
                        aria-label="First page of the second document"
                        onClick={stop}
                      />
                      <button
                        type="button"
                        className="ev-btn ev-btn-sm ev-btn-outline"
                        onClick={(e) => {
                          stop(e);
                          onSplitConfirm(i, Number(splitRef.current?.value));
                        }}
                      >
                        Split
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-sm ev-btn-ghost"
                        onClick={(e) => {
                          stop(e);
                          onSplitCancel();
                        }}
                      >
                        Cancel
                      </button>
                    </span>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
