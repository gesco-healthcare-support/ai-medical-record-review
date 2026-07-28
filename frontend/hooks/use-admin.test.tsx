import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/admin-api", () => ({
  putPrompt: vi.fn().mockResolvedValue({ category_id: "3", text: "x", custom: true }),
  deletePrompt: vi.fn().mockResolvedValue({ category_id: "3", text: null, custom: false }),
}));

import { useRevertPrompt, useSavePrompt } from "@/hooks/use-admin";

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
