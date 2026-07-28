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

/** Prompt writes flip a category's built-in/custom state, so BOTH the category list and that
 *  category's cached prompt must be refetched - without the second one, reopening the dialog after a
 *  save shows the pre-save state and cannot offer "Revert to built-in". */
function usePromptMutation<TArgs>(
  mutationFn: (args: TArgs) => Promise<unknown>,
  categoryIdOf: (args: TArgs) => string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (_data, args) => {
      queryClient.invalidateQueries({ queryKey: CATEGORIES_KEY });
      queryClient.invalidateQueries({ queryKey: ["admin", "prompt", categoryIdOf(args)] });
    },
  });
}

export const useSavePrompt = () =>
  usePromptMutation(
    (vars: { id: string; text: string }) => api.putPrompt(vars.id, vars.text),
    (vars) => vars.id,
  );

/** Reverting drops the custom row so the built-in (code) prompt applies again. */
export const useRevertPrompt = () =>
  usePromptMutation(
    (id: string) => api.deletePrompt(id),
    (id) => id,
  );

/** Reprocess does not change the catalog, so it needs no invalidation. */
export const useReprocess = () =>
  useMutation({ mutationFn: (documentId: string) => api.reprocessDocument(documentId) });
