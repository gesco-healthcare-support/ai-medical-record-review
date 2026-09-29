import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/documents-api";
import type { DocumentListItem } from "@/lib/types";

const DOCS_KEY = ["documents"] as const;

/** The documents list. Polls every 2s while any record has an active job, then stops.
 *
 *  `owner` (an admin looking at another reviewer's records) gets its own cache entry under the
 *  same prefix, so the mutations below, which invalidate `["documents"]`, refresh it too. With no
 *  owner the key is exactly what it always was. */
export function useDocuments(owner?: number | null) {
  return useQuery({
    queryKey: owner == null ? DOCS_KEY : [...DOCS_KEY, "owner", owner],
    queryFn: () => api.listDocuments(owner),
    refetchInterval: (query) => {
      const docs = (query.state.data ?? []) as DocumentListItem[];
      return docs.some((doc) => doc.active_job) ? 2000 : false;
    },
  });
}

function useDocsMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: DOCS_KEY }),
  });
}

export const useUploadDocument = () => useDocsMutation((file: File) => api.uploadDocument(file));
export const useAggregateDocuments = () =>
  useDocsMutation((vars: { name: string; files: File[] }) =>
    api.aggregateDocuments(vars.name, vars.files),
  );
export const useDeleteDocument = () => useDocsMutation((id: string) => api.deleteDocument(id));
export const useStartIdentification = () =>
  useDocsMutation((id: string) => api.startIdentification(id));
