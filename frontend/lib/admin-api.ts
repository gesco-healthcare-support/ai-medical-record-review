import { apiFetch } from "@/lib/api";

/** A category row from /api/admin/categories (Category.listing() + has_summary_prompt). */
export type AdminCategory = {
  id: string;
  name: string;
  description: string;
  examples: string[];
  active: boolean;
  auto_assign: boolean;
  summarize_default: boolean;
  has_summary_prompt: boolean;
};

/** GET /api/admin/prompts/{id}: the stored custom prompt (if any), the effective text, and the
 *  built-in prompt from the app code that a revert would restore. */
export type PromptInfo = {
  category_id: string;
  text: string | null;
  effective_text: string;
  builtin_text: string;
  custom: boolean;
};

/** The editable fields of a category (id is create-only and immutable). */
export type CategoryInput = {
  name: string;
  description: string;
  examples: string[];
  auto_assign: boolean;
  summarize_default: boolean;
  active: boolean;
};

export function listCategories() {
  return apiFetch<AdminCategory[]>("/admin/categories");
}

export function createCategory(body: CategoryInput & { id: string }) {
  return apiFetch<AdminCategory>("/admin/categories", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateCategory(id: string, body: Partial<CategoryInput>) {
  return apiFetch<AdminCategory>(`/admin/categories/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function getPrompt(id: string) {
  return apiFetch<PromptInfo>(`/admin/prompts/${id}`);
}

export function putPrompt(id: string, text: string) {
  return apiFetch<{ category_id: string; text: string; custom: boolean }>(`/admin/prompts/${id}`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });
}

/** DELETE /api/admin/prompts/{id}: drop the custom row so the built-in (code) prompt applies. */
export function deletePrompt(id: string) {
  return apiFetch<PromptInfo>(`/admin/prompts/${id}`, { method: "DELETE" });
}

/** Re-summarize any owner's document with the current prompts (admin-scoped). */
export function reprocessDocument(documentId: string) {
  return apiFetch<{ ok: boolean }>(`/admin/reprocess/${documentId}`, { method: "POST" });
}
