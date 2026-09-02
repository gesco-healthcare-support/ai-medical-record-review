"use client";

import { Fragment, useRef, type MouseEvent } from "react";
import { cn } from "@/lib/utils";
import type { CategoryOption, Row } from "@/lib/types";
import type { EditorRow } from "@/lib/review-rows";

/** Exported so the Summaries tab's per-card select offers the IDENTICAL list, including the
 *  synthesized entry for a `current` value the catalog no longer carries: two divergent option lists
 *  for one field is how a reviewer ends up unable to see what a row is actually set to. */
export function categoryOptions(categories: CategoryOption[], current: string) {
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
 * The sub-documents table (DS #rowsTable). Two rows per document on purpose: the title gets its own
 * full-width top line (with the per-row tools), and the dense fields - page range, category, date,
 * injury date, and the review/summarize checkboxes - sit on the second line. Gap strips mark skipped
 * pages between non-contiguous rows. Purely presentational: every edit is emitted via a callback and
 * the parent owns the rows + autosave.
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
  attentionPages,
  unidentifiedKeys,
  guessedKeys,
  hiddenKeys,
}: Readonly<{
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
  attentionPages?: Set<string>;
  /** _keys of the rows nothing identified (issue #144). Chipped, never removed. */
  unidentifiedKeys?: Set<string>;
  /** Rows whose category the cascade GUESSED - a real category, but not a settled one.
   *  Separate from `unidentifiedKeys` because the two chips say different things. */
  guessedKeys?: Set<string>;
  /** _keys the "could not identify" filter is hiding. Rows are dropped INSIDE the map below,
   *  never by narrowing this array: `#` is `i + 1` and the gap strips come from `previousEnd`,
   *  so a filtered array would renumber every document and invent gaps that are not real. */
  hiddenKeys?: Set<string>;
}>) {
  const splitRef = useRef<HTMLInputElement>(null);
  let previousEnd = 0;
  // Between two rows the filter has separated, a gap strip would fire on every one of them and
  // none would mean what it says, so the strips stand down while anything is hidden.
  const filtering = (hiddenKeys?.size ?? 0) > 0;

  return (
    <table id="rowsTable">
      <thead>
        <tr>
          <th className="col-num">#</th>
          <th className="col-page">Start</th>
          <th className="col-page">End</th>
          <th className="col-category">Category</th>
          <th className="col-date">Date</th>
          <th className="col-date">Injury date</th>
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
          // Hidden only AFTER previousEnd has advanced: the gap arithmetic has to see every
          // row, and the number below is `i + 1` over the full set, so a hidden document keeps
          // its place in the numbering rather than shifting the ones under it.
          if (hiddenKeys?.has(row._key)) return null;
          const titleValue = row.title && row.title !== "-" ? row.title : "";
          // A sub-document a needs_attention summarize run could not process (matched by page range).
          const failed = attentionPages?.has(`${row.start}-${row.end}`) ?? false;
          // A sub-document that landed in General with no rule putting it there.
          const unidentified = unidentifiedKeys?.has(row._key) ?? false;
          const guessed = guessedKeys?.has(row._key) ?? false;

          return (
            <Fragment key={row._key}>
              {showGap && !filtering ? (
                <tr className="gap-row">
                  <td colSpan={9}>
                    pages {gapFrom}-{gapTo} not included (skipped at summarization)
                  </td>
                </tr>
              ) : null}

              <tr
                className={cn(
                  "doc-row title-row",
                  selected === i && "selected",
                  !included && "skipped",
                  failed && "attention",
                )}
                onClick={() => onSelect(i)}
              >
                <td className="col-num rc-titletd">{i + 1}</td>
                <td colSpan={8} className="rc-titletd">
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
                    {failed ? (
                      <span
                        className="rc-attn-chip"
                        title="This document could not be summarized - exclude it or fix its pages, then summarize again"
                      >
                        Could not summarize
                      </span>
                    ) : null}
                    {/* Deliberately not amber and with no row background: a row can carry both
                        chips, and two amber signals on one line cannot be told apart. */}
                    {unidentified ? (
                      <span
                        className="rc-unid-chip"
                        title="Nothing identified this document - it is in General because no rule or model could name it"
                      >
                        Could not identify
                      </span>
                    ) : null}
                    {/* Mutually exclusive with the chip above by construction - categoryWasGuessed
                        skips General - so a row never carries both, which is the same reason the
                        comment above keeps this column to one signal. */}
                    {guessed ? (
                      <span
                        className="rc-unid-chip"
                        title="The category was a guess: no rule matched and the two classifiers did not agree. This document IS being summarized under it."
                      >
                        Category guessed
                      </span>
                    ) : null}
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
                  failed && "attention",
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
                    aria-label="First page"
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
                    aria-label="Last page"
                    onClick={stop}
                    onChange={(e) => onField(i, { end: Number(e.target.value) })}
                  />
                </td>
                <td>
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
                <td>
                  <input
                    type="text"
                    className="rc-inp"
                    value={row.date}
                    aria-label="Document date"
                    onClick={stop}
                    onChange={(e) => onField(i, { date: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    type="text"
                    className="rc-inp"
                    value={row.injury_date}
                    aria-label="Injury date"
                    onClick={stop}
                    onChange={(e) => onField(i, { injury_date: e.target.value })}
                  />
                </td>
                <td className="col-check">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    aria-label="Flag for manual review"
                    checked={String(row.flag).toLowerCase() === "x"}
                    onClick={stop}
                    onChange={(e) => onField(i, { flag: e.target.checked ? "x" : "-" })}
                  />
                </td>
                <td className="col-check col-sum">
                  <input
                    type="checkbox"
                    className="ev-cb"
                    aria-label="Include in summarization"
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
