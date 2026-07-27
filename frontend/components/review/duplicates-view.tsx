"use client";

import { useRef, useState } from "react";
import { Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useDuplicates, useResolveDuplicate } from "@/hooks/use-duplicates";
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
}: {
  documentId: string;
  filename?: string;
  onResolved?: () => void;
}) {
  const { data, isLoading, error } = useDuplicates(documentId);
  const resolve = useResolveDuplicate(documentId);
  const pdfRef = useRef<PdfViewerHandle>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const job = data?.job;
  const running = job?.state === "queued" || job?.state === "running";
  const clusters = data?.clusters ?? [];
  // Boundaries changed since the last check: point at the header's "Re-check duplicates" (a re-run
  // is always manual - it costs AI calls). Hidden while a check is already in flight.
  const stale = Boolean(data?.stale) && !running;

  function openRow(row: DuplicateRow) {
    setSelectedIdx(row.idx);
    pdfRef.current?.jumpTo(row.pages.start);
  }

  async function act(group: number, action: "keep_one" | "dismiss", primaryIdx?: number) {
    setMsg("");
    try {
      await resolve.mutateAsync({ group, action, primaryIdx });
      onResolved?.();
    } catch (err) {
      setMsg(humanizeError(err, { fallback: "Could not save - please try again." }));
    }
  }

  const loadError = error ? humanizeError(error, { fallback: "Could not load duplicates." }) : "";

  const countLine = clusters.length
    ? `${clusters.length} possible duplicate ${clusters.length === 1 ? "group" : "groups"}`
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
                  ? `Checking for duplicates${job?.total ? ` (${job.current}/${job.total})` : "..."}`
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

        {isLoading ? null : clusters.length === 0 ? (
          <div className="summary-empty">
            <Copy width={34} height={34} aria-hidden />
            <p className="empty-title">{running ? "Checking for duplicates..." : "No duplicates"}</p>
            <p>
              {running
                ? "Scanning the record for documents that were scanned more than once."
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
                onKeep={(idx) => act(cluster.group, "keep_one", idx)}
                onDismiss={() => act(cluster.group, "dismiss")}
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
}: {
  cluster: DuplicateCluster;
  busy: boolean;
  selectedIdx: number | null;
  onOpen: (row: DuplicateRow) => void;
  onKeep: (idx: number) => void;
  onDismiss: () => void;
}) {
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
      <ul className="dupe-copies">
        {cluster.rows.map((row) => (
          // The row is a plain <li> (not a button) so the actions inside stay valid; clicking
          // anywhere opens the copy in the viewer, and the title button is the keyboard path.
          <li
            key={row.idx}
            className={cn(
              "dupe-copy",
              "clickable",
              row.primary && "primary",
              selectedIdx === row.idx && "selected",
            )}
            onClick={() => onOpen(row)}
          >
            <span className="meta">
              {row.date && row.date !== "-" ? row.date : "no date"} - pages {row.pages.start}
              {"–"}
              {row.pages.end}
              {row.primary ? " - kept" : row.include === false ? " - excluded" : ""}
            </span>
            <button
              type="button"
              className="row-jump dupe-copy-title"
              onClick={(e) => {
                e.stopPropagation();
                onOpen(row);
              }}
            >
              {row.title && row.title !== "-" ? row.title : "Untitled"}
            </button>
            {!cluster.dismissed ? (
              <button
                type="button"
                className="ev-btn ev-btn-ghost ev-btn-sm"
                disabled={busy || row.primary}
                onClick={(e) => {
                  e.stopPropagation();
                  onKeep(row.idx);
                }}
              >
                {row.primary ? "Kept" : "Keep this one"}
              </button>
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
