import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/admin-api";
import type { CategoryInput } from "@/lib/admin-api";

const CATEGORIES_KEY = ["admin", "categories"] as const;

/** The category catalog (admin view). */
export function useCategories() {
  return useQuery({ queryKey: CATEGORIES_KEY, queryFn: api.listCategories });
}

function useCategoryMutation<TArgs>(mutationFn: (args: TArgs) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CATEGORIES_KEY }),
  });
}

export const useCreateCategory = () =>
  useCategoryMutation((body: CategoryInput & { id: string }) => api.createCategory(body));

export const useUpdateCategory = () =>
  useCategoryMutation((vars: { id: string; body: Partial<CategoryInput> }) =>
    api.updateCategory(vars.id, vars.body),
  );

/** Saving a prompt can flip a category's Custom/General badge, so refresh the list too. */
export const useSavePrompt = () =>
  useCategoryMutation((vars: { id: string; text: string }) => api.putPrompt(vars.id, vars.text));

/** Reprocess does not change the catalog, so it needs no invalidation. */
export const useReprocess = () =>
  useMutation({ mutationFn: (documentId: string) => api.reprocessDocument(documentId) });
