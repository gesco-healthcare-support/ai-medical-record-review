"use client";

import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSummaries, putSummary, resummarize } from "@/lib/review-api";
import type { SummaryItem } from "@/lib/types";

/** react-query key for one record's drafted summaries. Shared so the review header's tab count and
 *  the summaries view resolve to a single fetch (query dedupe), and edits update both in place. */
export function summariesKey(documentId: string) {
  return ["summaries", documentId] as const;
}

/** The drafted summaries for a record. Enabled always (returns [] before summarization), so the
 *  Summaries tab can show its count without the summaries view being mounted. */
export function useSummaries(documentId: string) {
  return useQuery({
    queryKey: summariesKey(documentId),
    queryFn: () => getSummaries(documentId),
  });
}

/** Replace one summary in the cache after a server mutation returns the updated item. */
function useSummaryPatch(documentId: string) {
  const queryClient = useQueryClient();
  return (updated: SummaryItem) =>
    queryClient.setQueryData<SummaryItem[]>(summariesKey(documentId), (prev) =>
      (prev ?? []).map((s) => (s.idx === updated.idx ? updated : s)),
    );
}

/** Re-read the whole list. For a change that reaches beyond the one summary the server returned: a
 *  page holding several visits has one summary per visit, all on one row. */
function useSummariesRefresh(documentId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: summariesKey(documentId) });
}

/** Edit a summary (title/date/text), toggle its export inclusion, or re-classify it (`category`,
 *  which the server writes to the owning ReviewRow). A category change re-reads the list: it lands
 *  on the ROW, so every other visit of that row changed too. */
export function useSaveSummary(documentId: string) {
  const patch = useSummaryPatch(documentId);
  const refresh = useSummariesRefresh(documentId);
  return useMutation({
    mutationFn: (vars: {
      idx: number;
      body: Partial<{
        summaryTitle: string;
        summaryDate: string;
        summaryText: string;
        excluded: boolean;
        category: string;
      }>;
    }) => putSummary(documentId, vars.idx, vars.body),
    onSuccess: (updated, vars) => {
      patch(updated);
      if ("category" in vars.body) void refresh();
    },
  });
}

/** react-query key of one record's re-drafts. A re-draft is one request held open for the whole
 *  model call, and a pending one lives in the client's mutation cache, which outlives the Summaries
 *  view: a reviewer who goes to My documents while a re-draft computes, then comes back, finds the
 *  card still "Re-drafting...". The view used to read the state of its own mutation instead, which
 *  ended with the view and named only the latest re-draft, so a second one made the first card look
 *  idle while it was still running (reviewer report, 2026-11-02). */
export function redraftKey(documentId: string) {
  return ["resummarize", documentId] as const;
}

/** How many of a record's summaries may be re-drafting at once. Each holds a request open for the
 *  whole model call, and over HTTP/1.1 a browser opens only six connections to one server, so more
 *  would queue the page images and status polls behind them. */
export const MAX_PARALLEL_REDRAFTS = 3;

/** The indices of the record's summaries whose re-draft is still running. */
export function usePendingRedrafts(documentId: string): ReadonlySet<number> {
  const pending = useMutationState({
    filters: { mutationKey: redraftKey(documentId), status: "pending" },
    select: (mutation) => mutation.state.variables as number,
  });
  return new Set(pending);
}

/** Re-draft one summary (discards evaluator edits, writes a fresh AI draft). Re-reads the list as
 *  well: the re-draft rewrites every visit of the summary's row, and can find more or fewer visits
 *  than before, which adds or removes summaries and renumbers the ones after them.
 *
 *  A REFUSED re-draft re-reads it too. The server refuses (409) when a summarize run laid the
 *  summaries out again while the draft was computing, and then the cards on screen are the ones
 *  that moved: a card left in place would send its next re-draft to the summary now at its index,
 *  which can be another document's, and a re-draft discards that summary's edits. */
export function useResummarize(documentId: string) {
  const patch = useSummaryPatch(documentId);
  const refresh = useSummariesRefresh(documentId);
  return useMutation({
    mutationKey: redraftKey(documentId),
    mutationFn: (idx: number) => resummarize(documentId, idx),
    onSuccess: (updated) => {
      patch(updated);
      void refresh();
    },
    onError: () => {
      void refresh();
    },
  });
}
