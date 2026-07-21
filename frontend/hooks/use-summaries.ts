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

/** Edit a summary (title/date/text) or toggle its export inclusion. */
export function useSaveSummary(documentId: string) {
  const patch = useSummaryPatch(documentId);
  return useMutation({
    mutationFn: (vars: {
      idx: number;
      body: Partial<{
        summaryTitle: string;
        summaryDate: string;
        summaryText: string;
        excluded: boolean;
      }>;
    }) => putSummary(documentId, vars.idx, vars.body),
    onSuccess: patch,
  });
}

/** Re-draft one summary (discards evaluator edits, writes a fresh AI draft). */
export function useResummarize(documentId: string) {
  const patch = useSummaryPatch(documentId);
  return useMutation({
    mutationFn: (idx: number) => resummarize(documentId, idx),
    onSuccess: patch,
  });
}
