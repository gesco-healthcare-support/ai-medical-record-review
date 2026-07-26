"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getDuplicates, resolveDuplicate, startDedup } from "@/lib/review-api";

/** react-query key for one record's duplicate clusters (shared by the tab badge + the view). */
export function duplicatesKey(documentId: string) {
  return ["duplicates", documentId] as const;
}

/** Confirmed duplicate clusters + dedup job progress. Polls every 2s WHILE a dedup job is running so
 *  the tab fills in as clustering completes; idle otherwise. */
export function useDuplicates(documentId: string) {
  return useQuery({
    queryKey: duplicatesKey(documentId),
    queryFn: () => getDuplicates(documentId),
    refetchInterval: (query) => {
      const state = query.state.data?.job?.state;
      return state === "queued" || state === "running" ? 2000 : false;
    },
  });
}

/** Resolve one cluster (keep-one or dismiss); refetch the clusters afterward. */
export function useResolveDuplicate(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { group: number; action: "keep_one" | "dismiss"; primaryIdx?: number }) =>
      resolveDuplicate(documentId, vars.group, vars.action, vars.primaryIdx),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: duplicatesKey(documentId) }),
  });
}

/** Manually (re)run duplicate clustering. */
export function useStartDedup(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => startDedup(documentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: duplicatesKey(documentId) }),
  });
}
