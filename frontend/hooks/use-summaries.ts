"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

/** Re-draft one summary (discards evaluator edits, writes a fresh AI draft). Re-reads the list as
 *  well: the re-draft rewrites every visit of the summary's row, and can find more or fewer visits
 *  than before, which adds or removes summaries and renumbers the ones after them. */
export function useResummarize(documentId: string) {
  const patch = useSummaryPatch(documentId);
  const refresh = useSummariesRefresh(documentId);
  return useMutation({
    mutationFn: (idx: number) => resummarize(documentId, idx),
    onSuccess: (updated) => {
      patch(updated);
      void refresh();
    },
  });
}
