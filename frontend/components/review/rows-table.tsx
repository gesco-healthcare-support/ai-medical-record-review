"use client";

import { Fragment, useRef, type MouseEvent } from "react";
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
 * The sub-documents table (DS #rowsTable). One title row + one field row per document, gap
 * strips between non-contiguous rows, and per-row tools (merge up / split / delete). Purely
 * presentational: every edit is emitted via a callback; the parent owns the rows + autosave.
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
          <th className="col-num">#</th>
          <th className="col-page">Start</th>
          <th className="col-page">End</th>
          <th className="col-category">Category</th>
          <th className="col-date">Date</th>
          <th className="col-check" title="Flag for manual review">
            Review
          </th>
          <th className="col-check col-sum" title="Include this document in summarization">
            Summarize
          </th>
          <th className="col-actions" aria-label="Row actions" />
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

          return (
            <Fragment key={row._key}>
              {showGap ? (
                <tr className="gap-row">
                  <td colSpan={8}>
                    pages {gapFrom}-{gapTo} not included (skipped at summarization)
                  </td>
                </tr>
              ) : null}

              <tr
                className={cn(
                  "doc-row title-row",
                  selected === i && "selected",
                  !included && "skipped",
                )}
                onClick={() => onSelect(i)}
              >
                <td className="col-num rc-titletd">{i + 1}</td>
                <td colSpan={7} className="rc-titletd">
                  <div className="rc-titlebar">
                    <input
                      type="text"
                      className="rc-title"
                      placeholder="(untitled document)"
                      aria-label="Document title"
                      value={titleValue}
                      onClick={stop}
                      onChange={(e) => onField(i, { title: e.target.value })}
                    />
                    <span className="rc-rowactions">
                      {splitting === i ? (
                        <>
                          at page{" "}
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
                        </>
                      ) : (
                        <>
                          {row.suggest_merge && i > 0 ? (
                            <button
                              type="button"
                              className="ev-btn ev-btn-sm ev-btn-gold"
                              title="The AI double-checked this boundary and believes it continues the document above"
                              onClick={(e) => {
                                stop(e);
                                onMergeUp(i);
                              }}
                            >
                              Likely same doc {"—"} merge?
                            </button>
                          ) : null}
                          {i > 0 ? (
                            <button
                              type="button"
                              className="ev-btn ev-btn-sm ev-btn-outline"
                              title="Merge into the document above"
                              onClick={(e) => {
                                stop(e);
                                onMergeUp(i);
                              }}
                            >
                              Merge up
                            </button>
                          ) : null}
                          {Number(row.end) > Number(row.start) ? (
                            <button
                              type="button"
                              className="ev-btn ev-btn-sm ev-btn-outline"
                              title="Split this document into two"
                              onClick={(e) => {
                                stop(e);
                                onSplitStart(i);
                              }}
                            >
                              Split
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="ev-btn ev-btn-sm ev-btn-del"
                            title="Remove this row"
                            onClick={(e) => {
                              stop(e);
                              onDelete(i);
                            }}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                </td>
              </tr>

              <tr
                className={cn(
                  "doc-row",
                  errors.has(i) && "invalid",
                  selected === i && "selected",
                  !included && "skipped",
                )}
                onClick={() => onSelect(i)}
              >
                <td className="col-num" />
                <td>
                  <input
                    type="number"
                    className="rc-inp"
                    value={row.start}
                    min={1}
                    max={totalPages}
                    onClick={stop}
                    onChange={(e) => onField(i, { start: Number(e.target.value) })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    className="rc-inp"
                    value={row.end}
                    min={1}
                    max={totalPages}
                    onClick={stop}
                    onChange={(e) => onField(i, { end: Number(e.target.value) })}
                  />
                </td>
                <td>
                  <span className="rc-selwrap">
                    <select
                      className="rc-sel"
                      value={row.category}
                      onClick={stop}
                      onChange={(e) => onField(i, { category: e.target.value })}
                    >
                      {categoryOptions(categories, row.category)}
                    </select>
                  </span>
                </td>
                <td>
                  <input
                    type="text"
                    className="rc-inp"
                    value={row.date}
                    onClick={stop}
                    onChange={(e) => onField(i, { date: e.target.value })}
                  />
                </td>
                <td className="col-check">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    checked={String(row.flag).toLowerCase() === "x"}
                    onClick={stop}
                    onChange={(e) => onField(i, { flag: e.target.checked ? "x" : "-" })}
                  />
                </td>
                <td className="col-check col-sum">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    checked={included}
                    onClick={stop}
                    onChange={(e) => onField(i, { include: e.target.checked })}
                  />
                </td>
                <td />
              </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
