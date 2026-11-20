import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { assignDocument } from "@/lib/admin-api";
import * as api from "@/lib/documents-api";
import type { BillingSheetEdit } from "@/lib/billing-sheet";
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

/** One PDF, into the caller's own account or - for an admin - `owner`'s. */
export const useUploadDocument = () =>
  useDocsMutation(({ file, ...target }: { file: File } & api.UploadTarget) =>
    api.uploadDocument(file, target),
  );
export const useCombineReceivedFiles = () =>
  useDocsMutation((upload: api.ReceivedFilesUpload) => api.combineReceivedFiles(upload));
/** Admins: move a record to another reviewer. It leaves the list on screen, so the list refreshes. */
export const useAssignDocument = () =>
  useDocsMutation((vars: { id: string; owner: number; allowDuplicate?: boolean }) =>
    assignDocument(vars.id, vars.owner, vars.allowDuplicate),
  );
export const useDeleteDocument = () => useDocsMutation((id: string) => api.deleteDocument(id));

/** The reviewer's Completed mark. It shows at once - the cached list is patched before the server
 *  answers - and the list is read again afterwards, which also takes a refused change back. */
export function useSetCompleted() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; completed: boolean }) =>
      api.setCompleted(vars.id, vars.completed),
    onMutate: async ({ id, completed }) => {
      await queryClient.cancelQueries({ queryKey: DOCS_KEY });
      const completedAt = completed ? new Date().toISOString() : null;
      queryClient.setQueriesData<DocumentListItem[]>({ queryKey: DOCS_KEY }, (docs) =>
        Array.isArray(docs)
          ? docs.map((d) => (d.id === id ? { ...d, completed_at: completedAt } : d))
          : docs,
      );
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: DOCS_KEY }),
  });
}
export const useStartIdentification = () =>
  useDocsMutation((id: string) => api.startIdentification(id));

/** The doctors the upload dialog offers. Fetched once while the dialog is open; the list changes
 *  only with a release. */
export function useDoctors(enabled = true) {
  return useQuery({
    queryKey: ["doctors"],
    queryFn: api.listDoctors,
    enabled,
    staleTime: Infinity,
    select: (answer) => answer.doctors,
  });
}

/** How often the billing sheet is read again while the record's pages are still being read. */
export const BILLING_POLL_MS = 5000;

const billingKey = (id: string) => ["billing", id] as const;

/** A record's billing sheet. While its pages are still being read (`pages_unread` above 0) it is
 *  read again every few seconds, so the counts appear once they are known. `id` null reads nothing. */
export function useBillingSheet(id: string | null) {
  return useQuery({
    queryKey: billingKey(id ?? ""),
    queryFn: () => api.getBillingSheet(id as string),
    enabled: id !== null,
    refetchInterval: (query) => ((query.state.data?.pages_unread ?? 0) > 0 ? BILLING_POLL_MS : false),
  });
}

/** Save the billing sheet; the answer replaces the cached sheet. */
export function useSaveBillingSheet(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (edit: BillingSheetEdit) => api.saveBillingSheet(id, edit),
    onSuccess: (sheet) => queryClient.setQueryData(billingKey(id), sheet),
  });
}
