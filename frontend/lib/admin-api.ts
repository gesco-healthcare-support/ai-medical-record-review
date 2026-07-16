import { apiFetch } from "@/lib/api";

/** A category row from /api/admin/categories (Category.listing() + has_summary_prompt). */
export type AdminCategory = {
  id: string;
  name: string;
  description: string;
  examples: string[];
  active: boolean;
  auto_assign: boolean;
  has_summary_prompt: boolean;
};

/** GET /api/admin/prompts/{id}: the stored custom prompt (if any) + the effective text. */
export type PromptInfo = {
  category_id: string;
  text: string | null;
  effective_text: string;
  custom: boolean;
};

/** The editable fields of a category (id is create-only and immutable). */
export type CategoryInput = {
  name: string;
  description: string;
  examples: string[];
  auto_assign: boolean;
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

/** Re-summarize any owner's document with the current prompts (admin-scoped). */
export function reprocessDocument(documentId: string) {
  return apiFetch<{ ok: boolean }>(`/admin/reprocess/${documentId}`, { method: "POST" });
}
