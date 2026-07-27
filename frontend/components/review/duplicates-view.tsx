"use client";

import { useState } from "react";
import { Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { humanizeError } from "@/lib/errors";
import { useDuplicates, useResolveDuplicate, useStartDedup } from "@/hooks/use-duplicates";
import type { DuplicateCluster } from "@/lib/types";

/** Duplicates review (before summarization): each confirmed cluster lists its copies oldest-first;
 *  the reviewer keeps one copy (excluding the rest from summarization) or dismisses the cluster as
 *  not-duplicates. Advisory - it never blocks Summarize. `onResolved` lets the parent refresh the
 *  Review editor's rows so a later Summarize respects the exclusions. */
export function DuplicatesView({
  documentId,
  onResolved,
}: {
  documentId: string;
  onResolved?: () => void;
}) {
  const { data, isLoading, error } = useDuplicates(documentId);
  const resolve = useResolveDuplicate(documentId);
  const recheck = useStartDedup(documentId);
  const [msg, setMsg] = useState("");

  const job = data?.job;
  const running = job?.state === "queued" || job?.state === "running";
  const clusters = data?.clusters ?? [];
  // Boundaries changed since the last check: offer a MANUAL re-check (never automatic - a re-run
  // costs AI calls). Hidden while a check is already in flight.
  const stale = Boolean(data?.stale) && !running;

  async function onRecheck() {
    setMsg("");
    try {
      await recheck.mutateAsync();
    } catch (err) {
      setMsg(humanizeError(err, { fallback: "Could not start the check - please try again." }));
    }
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

  return (
    <section id="step-duplicates" className="sum-column">
      <div className="sum-header">
        <div>
          <h1>Duplicate documents</h1>
          <div className="sum-countline">
            <span>
              {running
                ? `Checking for duplicates${job?.total ? ` (${job.current}/${job.total})` : "..."}`
                : clusters.length
                  ? `${clusters.length} possible duplicate ${clusters.length === 1 ? "group" : "groups"}`
                  : "No duplicate documents found"}
            </span>
            <span className="muted">{msg || loadError}</span>
          </div>
        </div>
      </div>

      {stale ? (
        <div className="banner" aria-live="polite">
          <span>
            Document boundaries changed since the last duplicate check, so this list may be
            incomplete.
          </span>{" "}
          <button
            type="button"
            className="ev-btn ev-btn-ghost ev-btn-sm"
            disabled={recheck.isPending}
            onClick={onRecheck}
          >
            {recheck.isPending ? "Starting..." : "Re-check duplicates"}
          </button>
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
              onKeep={(idx) => act(cluster.group, "keep_one", idx)}
              onDismiss={() => act(cluster.group, "dismiss")}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ClusterCard({
  cluster,
  busy,
  onKeep,
  onDismiss,
}: {
  cluster: DuplicateCluster;
  busy: boolean;
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
          <li key={row.idx} className={cn("dupe-copy", row.primary && "primary")}>
            <span className="meta">
              {row.date && row.date !== "-" ? row.date : "no date"} - pages {row.pages.start}
              {"–"}
              {row.pages.end}
              {row.primary ? " - kept" : row.include === false ? " - excluded" : ""}
            </span>
            <span className="dupe-copy-title">{row.title && row.title !== "-" ? row.title : "Untitled"}</span>
            {!cluster.dismissed ? (
              <button
                type="button"
                className="ev-btn ev-btn-ghost ev-btn-sm"
                disabled={busy || row.primary}
                onClick={() => onKeep(row.idx)}
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
