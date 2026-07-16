import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/documents-api";
import type { DocumentListItem } from "@/lib/types";

const DOCS_KEY = ["documents"] as const;

/** The documents list. Polls every 2s while any record has an active job, then stops. */
export function useDocuments() {
  return useQuery({
    queryKey: DOCS_KEY,
    queryFn: api.listDocuments,
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
  useDocsMutation((files: File[]) => api.aggregateDocuments(files));
export const useDeleteDocument = () => useDocsMutation((id: string) => api.deleteDocument(id));
export const useStartIdentification = () =>
  useDocsMutation((id: string) => api.startIdentification(id));
