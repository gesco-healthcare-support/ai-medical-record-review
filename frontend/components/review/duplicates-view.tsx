"use client";

import { useRef, useState } from "react";
import { Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useDuplicates, useResolveDuplicate } from "@/hooks/use-duplicates";
import type { DuplicateAction } from "@/lib/review-api";
import type { DuplicateCluster, DuplicateRow } from "@/lib/types";
import { PdfViewer, type PdfViewerHandle } from "./pdf-viewer";
import { SplitPane } from "./split-pane";

/** Duplicates review (before summarization): each confirmed cluster lists its copies oldest-first
 *  beside the record's PDF, so the reviewer can read the pages before deciding; clicking a copy jumps
 *  the viewer to its first page. The reviewer keeps one copy (excluding the rest from summarization)
 *  or dismisses the cluster as not-duplicates. Advisory - it never blocks Summarize. `onResolved`
 *  lets the parent refresh the Review editor's rows so a later Summarize respects the exclusions. */
export function DuplicatesView({
  documentId,
  filename,
  onResolved,
}: Readonly<{
  documentId: string;
  filename?: string;
  onResolved?: () => void;
}>) {
  const { data, isLoading, error } = useDuplicates(documentId);
  const resolve = useResolveDuplicate(documentId);
  const pdfRef = useRef<PdfViewerHandle>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const job = data?.job;
  const running = job?.state === "queued" || job?.state === "running";
  // Both places that announce the running check render this identical suffix; naming it once
  // keeps them from drifting and avoids nesting a template literal inside another one.
  const checkingSuffix = job?.total ? ` (${job.current}/${job.total})` : "...";
  const clusters = data?.clusters ?? [];
  const unreadable = data?.unreadable ?? 0;
  // The last check ran and did not finish. Nothing said so: `checked` is false for a failed job, so
  // this fell into `neverChecked` and the counter silently reverted to "Not checked yet" mid-run -
  // a failed check was indistinguishable from one nobody had started, and `job.error` was fetched
  // and never read by anything. Cancelled is deliberately not an error: the reviewer did that.
  const failed = job?.state === "error" || job?.state === "interrupted";
  // No check has ever finished on this record. Distinct from "checked and clean" and it must not read
  // as it: dedup only runs when someone asks, so this is the state a record sits in by default.
  // Also distinct from a check that FAILED, which has its own banner and its own next step.
  const neverChecked = data !== undefined && !data.checked && !running && !failed;
  // Boundaries changed since the last check: point at the header's "Re-check duplicates" (a re-run
  // is always manual - it costs AI calls). Hidden while a check is already in flight.
  const stale = Boolean(data?.stale) && !running;

  function openRow(row: DuplicateRow) {
    setSelectedIdx(row.idx);
    pdfRef.current?.jumpTo(row.pages.start);
  }

  async function act(
    group: number,
    action: DuplicateAction,
    opts: { primaryIdx?: number; idx?: number } = {},
  ) {
    setMsg("");
    try {
      await resolve.mutateAsync({ group, action, ...opts });
      onResolved?.();
    } catch (err) {
      setMsg(humanizeError(err, { fallback: "Could not save - please try again." }));
    }
  }

  /** Dropping one copy out of a mixed cluster. Removals are keyed on the cluster's exact set of page
   *  ranges, so a later re-check re-forms the cluster WITH this copy back in it - say so up front
   *  rather than let the reviewer discover it after pruning a seven-member group. */
  function removeMember(group: number, row: DuplicateRow) {
    if (
      !window.confirm(
        `Remove pages ${row.pages.start}-${row.pages.end} from this group? ` +
          "It stops being treated as a duplicate. Re-checking duplicates later will ask about it again.",
      )
    ) {
      return;
    }
    void act(group, "remove_member", { idx: row.idx });
  }

  const loadError = error ? humanizeError(error, { fallback: "Could not load duplicates." }) : "";

  const countLine = clusters.length
    ? `${clusters.length} possible duplicate ${clusters.length === 1 ? "group" : "groups"}`
    : neverChecked
      ? "Not checked yet"
      : "No duplicate documents found";

  const list = (
    <div className="rce-splitcol">
      <div className="sum-column">
        <div className="sum-header">
          <div>
            <h1>Duplicate documents</h1>
            <div className="sum-countline">
              <span>
                {running
                  ? `Checking for duplicates${checkingSuffix}`
                  : countLine}
              </span>
              <span className="muted">{msg || loadError}</span>
            </div>
          </div>
        </div>

        {stale ? (
          <div className="banner" aria-live="polite">
            <span>
              Document boundaries changed since the last duplicate check, so this list may be
              incomplete. Use &quot;Re-check duplicates&quot; above to scan again.
            </span>
          </div>
        ) : null}

        {/* Never checked is not a result at all. The banner above warns when a COMPLETED check may
            be incomplete; this one covers the case that check never happened, which is the default
            state of every record because dedup is only ever started by hand. */}
        {neverChecked ? (
          <div className="banner" aria-live="polite">
            <span>
              No duplicate check has run on this record yet, so nothing here has been compared. Use
              &quot;Re-check duplicates&quot; above to scan for documents that were scanned more than
              once.
            </span>
          </div>
        ) : null}

        {/* A check that ran and failed. Without this the tab reverted to "Not checked yet" with no
            error text, so the reviewer could not tell a failure from a mis-click, and re-clicking
            produced the same silent outcome. When an earlier run DID complete, its clusters are
            still stored and still listed, so the wording has to hold for both. */}
        {failed ? (
          <div className="banner" aria-live="polite">
            <span>
              The last duplicate check did not finish
              {job?.error ? `: ${job.error}` : "."} Use &quot;Re-check duplicates&quot; above to try
              again.
              {data?.checked
                ? " The groups below are from the last check that completed."
                : " Nothing in this record has been compared yet."}
            </span>
          </div>
        ) : null}

        {/* A run that could not read part of the record is not a clean result: text-free
            sub-documents match nothing, so any duplicate involving them was never even considered.
            Silence here reads as "no duplicates", which is the wrong conclusion to hand a reviewer. */}
        {unreadable > 0 && !running ? (
          <div className="banner" aria-live="polite">
            <span>
              {unreadable} sub-document{unreadable === 1 ? "" : "s"} could not be read (no text
              recognized) and {unreadable === 1 ? "was" : "were"} not compared. Scanned images,
              photographs and blank separator pages have no text; duplicates among them cannot be
              found automatically.
            </span>
          </div>
        ) : null}

        {isLoading ? null : clusters.length === 0 ? (
          <div className="summary-empty">
            <Copy width={34} height={34} aria-hidden />
            {/* The counter belongs HERE, not only in the count line above: a large record takes tens
                of minutes (1498 pages measured at ~47), and a static "Checking for duplicates..."
                in the middle of an empty tab is indistinguishable from a hung job - which is exactly
                how it was reported. */}
            {/* `failed` has to come before the clean result: with no stored clusters a check that
                died would otherwise read as "No duplicates", which is the one conclusion a failed
                run cannot support. */}
            <p className="empty-title">
              {running
                ? `Checking for duplicates${checkingSuffix}`
                : failed
                  ? "Check did not finish"
                  : neverChecked
                    ? "Not checked yet"
                    : "No duplicates"}
            </p>
            <p>
              {running
                ? "Scanning the record for documents that were scanned more than once."
                : failed
                  ? "The last duplicate check stopped before it could compare this record."
                  : neverChecked
                    ? "This record has not been scanned for duplicates."
                    : "The record has no groups of duplicate documents to review."}
            </p>
          </div>
        ) : (
          <div className="summary-list">
            {clusters.map((cluster) => (
              <ClusterCard
                key={cluster.group}
                cluster={cluster}
                busy={resolve.isPending}
                selectedIdx={selectedIdx}
                onOpen={openRow}
                onKeep={(idx) => act(cluster.group, "keep_one", { primaryIdx: idx })}
                onDismiss={() => act(cluster.group, "dismiss")}
                onRemove={(row) => removeMember(cluster.group, row)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <section id="step-duplicates" className="rce-split">
      <SplitPane
        storageKey="mrr.duplicates.split"
        left={list}
        right={
          <div className="rce-viewer">
            <PdfViewer ref={pdfRef} documentId={documentId} filename={filename} />
          </div>
        }
      />
    </section>
  );
}

function ClusterCard({
  cluster,
  busy,
  selectedIdx,
  onOpen,
  onKeep,
  onDismiss,
  onRemove,
}: Readonly<{
  cluster: DuplicateCluster;
  busy: boolean;
  selectedIdx: number | null;
  onOpen: (row: DuplicateRow) => void;
  onKeep: (idx: number) => void;
  onDismiss: () => void;
  onRemove: (row: DuplicateRow) => void;
}>) {
  // Resolved = at most one copy would still be summarized (the reviewer kept one, or excluded the
  // rest by hand). Inclusion - not the "kept" mark - is the test, so a cluster stays resolved after a
  // re-check recomputes its group, and is flagged again if it gains another included copy.
  const includedCount = cluster.rows.filter((r) => r.include !== false).length;
  const resolved = includedCount < 2;
  return (
    <div className={cn("summary-card", cluster.dismissed && "excluded")}>
      <div className="summary-head">
        <h3 className="sum-heading">
          Possible duplicate - {cluster.rows.length} copies
        </h3>
        {cluster.dismissed ? (
          <span className="ev-chip ev-chip-neutral">Dismissed</span>
        ) : resolved ? (
          <span className="ev-chip ev-chip-edit">Resolved</span>
        ) : (
          <span className="ev-chip ev-chip-review">Needs review</span>
        )}
      </div>
      {/* Advisory only, so it reads as a plain number: a colour scale would imply a cut-off the app
          does not enforce. Absent on clusters stored before the score was kept. */}
      {typeof cluster.similarity === "number" ? (
        <p className="meta dupe-similarity">
          {Math.round(cluster.similarity * 100)}% of the text matches - a high score means one
          document scanned twice, a low one means forms that share a template.
        </p>
      ) : null}
      <ul className="dupe-copies">
        {cluster.rows.map((row) => (
          // The copy's date/pages/title are one BUTTON that opens it in the viewer: a click handler
          // on the <li> itself would be mouse-only (no keyboard path), and making the <li> the
          // control would nest "Keep this one" inside another interactive element.
          <li
            key={row.idx}
            className={cn(
              "dupe-copy",
              row.primary && "primary",
              selectedIdx === row.idx && "selected",
            )}
          >
            <button type="button" className="row-jump dupe-copy-main" onClick={() => onOpen(row)}>
              <span className="meta">
                {row.date && row.date !== "-" ? row.date : "no date"} - pages {row.pages.start}
                {"–"}
                {row.pages.end}
                {row.primary ? " - kept" : row.include === false ? " - excluded" : ""}
              </span>
              <span className="dupe-copy-title">
                {row.title && row.title !== "-" ? row.title : "Untitled"}
              </span>
            </button>
            {!cluster.dismissed ? (
              // One wrapper carries the auto-margin for BOTH buttons. Putting `margin-left: auto` on
              // each of them would distribute the row's free space BETWEEN them and strand the first
              // mid-row, which is what the single-button rule did once a second button appeared.
              <span className="dupe-copy-actions">
                <button
                  type="button"
                  className="ev-btn ev-btn-ghost ev-btn-sm"
                  disabled={busy || row.primary}
                  onClick={() => onKeep(row.idx)}
                >
                  {row.primary ? "Kept" : "Keep this one"}
                </button>
                {/* Per-copy escape from a MIXED cluster: real records produce 7-member groups
                    spanning 7 dates, where some copies belong and others do not. Dismissing the whole
                    group would discard the genuine duplicates along with the false ones. */}
                <button
                  type="button"
                  className="ev-btn ev-btn-ghost ev-btn-sm"
                  disabled={busy}
                  title="This copy is a different document - take it out of this group"
                  onClick={() => onRemove(row)}
                >
                  Not a duplicate
                </button>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="edit-actions">
        {!cluster.dismissed ? (
          <button type="button" className="ev-btn ev-btn-ghost" disabled={busy} onClick={onDismiss}>
            Not duplicates
          </button>
        ) : null}
      </div>
    </div>
  );
}
