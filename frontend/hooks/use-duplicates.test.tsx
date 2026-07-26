import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock the API layer so the hooks + review-api dedup functions run without a real fetch.
const apiFetch = vi.fn();
vi.mock("@/lib/api", () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

import { useDuplicates, useResolveDuplicate, useStartDedup } from "@/hooks/use-duplicates";

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("use-duplicates", () => {
  beforeEach(() => apiFetch.mockReset());

  it("fetches the clusters for a document", async () => {
    apiFetch.mockResolvedValue({ clusters: [], job: null });
    const { result } = renderHook(() => useDuplicates("d1"), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiFetch).toHaveBeenCalledWith("/documents/d1/duplicates");
  });

  it("resolves a cluster with keep_one", async () => {
    apiFetch.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useResolveDuplicate("d1"), { wrapper: makeWrapper() });
    await result.current.mutateAsync({ group: 3, action: "keep_one", primaryIdx: 2 });
    expect(apiFetch).toHaveBeenCalledWith(
      "/documents/d1/duplicates/3/resolve",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(apiFetch.mock.calls[0][1].body);
    expect(body).toEqual({ action: "keep_one", primary_idx: 2 });
  });

  it("starts a dedup run", async () => {
    apiFetch.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useStartDedup("d1"), { wrapper: makeWrapper() });
    await result.current.mutateAsync();
    expect(apiFetch).toHaveBeenCalledWith(
      "/documents/d1/dedup/start",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
