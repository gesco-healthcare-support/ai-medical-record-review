/**
 * The summaries cache patch.
 *
 * Both write hooks replace ONE summary in the cache from the server's response rather than
 * refetching the list. That is the whole point - an edit or a re-draft must not make the other
 * nineteen cards flicker - but it means the match is load-bearing: matching too widely overwrites
 * summaries the reviewer never touched, and matching too narrowly leaves the edited one stale.
 *
 * The fixture therefore holds SEVERAL summaries. Against a single-entry cache a broken match still
 * produces the right answer, so the test would pass either way.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/review-api", () => ({
  getSummaries: vi.fn().mockResolvedValue([]),
  putSummary: vi.fn(),
  resummarize: vi.fn(),
}));

import { putSummary, resummarize } from "@/lib/review-api";
import { summariesKey, useResummarize, useSaveSummary } from "@/hooks/use-summaries";

const summary = (idx: number, text: string) => ({
  idx,
  summaryTitle: `Summary ${idx}`,
  summaryDate: "01/02/2026",
  summaryText: text,
  excluded: false,
  edited: false,
  row: { start: idx + 1, end: idx + 1, category: "1" },
});

/** A client whose cache already holds three summaries, so a patch has neighbours to get wrong. */
function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(summariesKey("d1"), [
    summary(0, "first as written"),
    summary(1, "second as written"),
    summary(2, "third as written"),
  ]);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const cached = () =>
    (client.getQueryData(summariesKey("d1")) as ReturnType<typeof summary>[]).map(
      (s) => s.summaryText,
    );
  return { wrapper, cached };
}

describe("useSaveSummary", () => {
  it("replaces only the saved summary in the cache", async () => {
    vi.mocked(putSummary).mockResolvedValue(summary(1, "second, edited") as never);
    const { wrapper, cached } = harness();
    const { result } = renderHook(() => useSaveSummary("d1"), { wrapper });

    await result.current.mutateAsync({ idx: 1, body: { summaryText: "second, edited" } });

    expect(putSummary).toHaveBeenCalledWith("d1", 1, { summaryText: "second, edited" });
    await waitFor(() =>
      expect(cached()).toEqual(["first as written", "second, edited", "third as written"]),
    );
  });
});

describe("useResummarize", () => {
  it("replaces only the re-drafted summary in the cache", async () => {
    vi.mocked(resummarize).mockResolvedValue(summary(2, "third, re-drafted") as never);
    const { wrapper, cached } = harness();
    const { result } = renderHook(() => useResummarize("d1"), { wrapper });

    await result.current.mutateAsync(2);

    expect(resummarize).toHaveBeenCalledWith("d1", 2);
    await waitFor(() =>
      expect(cached()).toEqual(["first as written", "second as written", "third, re-drafted"]),
    );
  });
});
