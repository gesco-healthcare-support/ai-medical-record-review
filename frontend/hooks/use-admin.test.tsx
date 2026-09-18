import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/admin-api", () => ({
  putPrompt: vi.fn().mockResolvedValue({ category_id: "3", text: "x", custom: true }),
  deletePrompt: vi.fn().mockResolvedValue({ category_id: "3", text: null, custom: false }),
  listCategories: vi.fn().mockResolvedValue([{ id: "3", name: "Diagnostic studies" }]),
  createCategory: vi.fn().mockResolvedValue({ id: "9" }),
  updateCategory: vi.fn().mockResolvedValue({ id: "3" }),
  reprocessDocument: vi.fn().mockResolvedValue({ ok: true }),
}));

import {
  createCategory,
  listCategories,
  reprocessDocument,
  updateCategory,
} from "@/lib/admin-api";
import {
  useCategories,
  useCreateCategory,
  useReprocess,
  useRevertPrompt,
  useSavePrompt,
  useUpdateCategory,
} from "@/hooks/use-admin";

/** A prompt write must refetch the category's own prompt, not just the category list: the dialog
 *  reads ["admin","prompt",id] on open, so a stale entry there showed the pre-save state and hid
 *  "Revert to built-in" (caught live, 2026-07-28). */
function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated: unknown[] = [];
  vi.spyOn(client, "invalidateQueries").mockImplementation(async (filters) => {
    invalidated.push(filters?.queryKey);
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, invalidated };
}

describe("prompt mutations invalidate the right caches", () => {
  it("refetches the category list AND that category's prompt after a save", async () => {
    const { wrapper, invalidated } = harness();
    const { result } = renderHook(() => useSavePrompt(), { wrapper });
    await result.current.mutateAsync({ id: "3", text: "CUSTOM" });
    await waitFor(() => expect(invalidated).toHaveLength(2));
    expect(invalidated).toEqual([["admin", "categories"], ["admin", "prompt", "3"]]);
  });

  it("refetches both after a revert", async () => {
    const { wrapper, invalidated } = harness();
    const { result } = renderHook(() => useRevertPrompt(), { wrapper });
    await result.current.mutateAsync("3");
    await waitFor(() => expect(invalidated).toHaveLength(2));
    expect(invalidated).toEqual([["admin", "categories"], ["admin", "prompt", "3"]]);
  });
});

describe("the category catalog", () => {
  it("is fetched under the key the mutations invalidate", async () => {
    // The key is the contract between the query and every write below it: a query reading one key
    // while the writes invalidate another leaves the admin screen showing pre-save state for ever.
    const { wrapper } = harness();
    const { result } = renderHook(() => useCategories(), { wrapper });

    await waitFor(() => expect(result.current.data).toEqual([{ id: "3", name: "Diagnostic studies" }]));
    expect(listCategories).toHaveBeenCalled();
  });

  it("is refetched after a category is created or updated", async () => {
    const created = harness();
    const create = renderHook(() => useCreateCategory(), { wrapper: created.wrapper });
    await create.result.current.mutateAsync({ id: "9", name: "New category" } as never);
    await waitFor(() => expect(created.invalidated).toEqual([["admin", "categories"]]));
    expect(createCategory).toHaveBeenCalledWith({ id: "9", name: "New category" });

    const updated = harness();
    const update = renderHook(() => useUpdateCategory(), { wrapper: updated.wrapper });
    await update.result.current.mutateAsync({ id: "3", body: { name: "Renamed" } });
    await waitFor(() => expect(updated.invalidated).toEqual([["admin", "categories"]]));
    expect(updateCategory).toHaveBeenCalledWith("3", { name: "Renamed" });
  });

  it("is NOT refetched after a reprocess", async () => {
    // A negative guarantee, kept deliberately: reprocessing a document changes nothing in the
    // catalog, and an invalidation added here would refetch it on every reprocess for nothing.
    const { wrapper, invalidated } = harness();
    const { result } = renderHook(() => useReprocess(), { wrapper });

    await result.current.mutateAsync("d1");

    expect(reprocessDocument).toHaveBeenCalledWith("d1");
    expect(invalidated).toEqual([]);
  });
});
