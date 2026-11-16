/**
 * The summaries cache patch.
 *
 * Both write hooks replace ONE summary in the cache from the server's response rather than
 * refetching the list. That is the whole point - an edit must not make the other nineteen cards
 * flicker - but it means the match is load-bearing: matching too widely overwrites summaries the
 * reviewer never touched, and matching too narrowly leaves the edited one stale.
 *
 * A re-draft and a category change ALSO re-read the list, because they reach past the one summary
 * returned: a page holding several visits has one summary per visit on one row, a category change
 * lands on that row, and a re-draft can find more or fewer visits than before.
 *
 * The fixture therefore holds SEVERAL summaries. Against a single-entry cache a broken match still
 * produces the right answer, so the test would pass either way.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/lib/review-api", () => ({
  getSummaries: vi.fn().mockResolvedValue([]),
  putSummary: vi.fn(),
  resummarize: vi.fn(),
}));

import { putSummary, resummarize } from "@/lib/review-api";
import {
  summariesKey,
  usePendingRedrafts,
  useResummarize,
  useSaveSummary,
} from "@/hooks/use-summaries";

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
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const cached = () =>
    (client.getQueryData(summariesKey("d1")) as ReturnType<typeof summary>[]).map(
      (s) => s.summaryText,
    );
  return { wrapper, cached, invalidate };
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

describe("re-reading the list", () => {
  it("a re-draft re-reads the list, since it can add or remove a row's visit summaries", async () => {
    vi.mocked(resummarize).mockResolvedValue(summary(2, "third, re-drafted") as never);
    const { wrapper, invalidate } = harness();
    const { result } = renderHook(() => useResummarize("d1"), { wrapper });

    await result.current.mutateAsync(2);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: summariesKey("d1") });
  });

  it("a REFUSED re-draft re-reads the list too, and patches nothing", async () => {
    // The server refuses when a summarize run laid the summaries out again while the draft was
    // computing - the cards on screen are then the ones that moved, and a card left in place would
    // send its next re-draft to whatever summary now holds its index.
    vi.mocked(resummarize).mockRejectedValue(new Error("summaries changed"));
    const { wrapper, cached, invalidate } = harness();
    const { result } = renderHook(() => useResummarize("d1"), { wrapper });

    await expect(result.current.mutateAsync(2)).rejects.toThrow("summaries changed");

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: summariesKey("d1") }),
    );
    expect(cached()).toEqual(["first as written", "second as written", "third as written"]);
  });

  it("a category change re-reads the list, and a text edit does not", async () => {
    vi.mocked(putSummary).mockResolvedValue(summary(1, "second as written") as never);
    const { wrapper, invalidate } = harness();
    const { result } = renderHook(() => useSaveSummary("d1"), { wrapper });

    await result.current.mutateAsync({ idx: 1, body: { summaryText: "second, edited" } });
    expect(invalidate).not.toHaveBeenCalled();

    await result.current.mutateAsync({ idx: 1, body: { category: "5" } });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: summariesKey("d1") });
  });

  it("taking a summary out of the export re-reads the record the bundle page lists", async () => {
    vi.mocked(putSummary).mockResolvedValue(summary(1, "second as written") as never);
    const { wrapper, invalidate } = harness();
    const { result } = renderHook(() => useSaveSummary("d1"), { wrapper });

    await result.current.mutateAsync({ idx: 1, body: { summaryText: "second, edited" } });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["document", "d1"] });

    await result.current.mutateAsync({ idx: 1, body: { excluded: true } });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["document", "d1"] });
  });
});

describe("usePendingRedrafts", () => {
  /** Re-drafts that wait until the test finishes them. */
  function held() {
    const finish = new Map<number, () => void>();
    vi.mocked(resummarize).mockImplementation(
      (_documentId: string, idx: number) =>
        new Promise((resolve) =>
          finish.set(idx, () => resolve(summary(idx, "re-drafted") as never)),
        ) as never,
    );
    return finish;
  }

  it("lists every running re-draft, and outlives the hook that started them", async () => {
    const finish = held();
    const { wrapper } = harness();
    const starter = renderHook(() => useResummarize("d1"), { wrapper });
    const pending = renderHook(() => usePendingRedrafts("d1"), { wrapper });

    act(() => {
      starter.result.current.mutate(0);
      starter.result.current.mutate(2);
    });
    await waitFor(() => expect([...pending.result.current].sort()).toEqual([0, 2]));

    starter.unmount(); // the Summaries view goes away while both requests are open
    expect([...pending.result.current].sort()).toEqual([0, 2]);

    await act(async () => finish.get(0)?.());
    await waitFor(() => expect([...pending.result.current]).toEqual([2]));
  });

  it("is per record", async () => {
    held();
    const { wrapper } = harness();
    const starter = renderHook(() => useResummarize("d1"), { wrapper });
    const other = renderHook(() => usePendingRedrafts("d2"), { wrapper });

    act(() => starter.result.current.mutate(1));

    await waitFor(() => expect(vi.mocked(resummarize)).toHaveBeenCalledWith("d1", 1));
    expect(other.result.current.size).toBe(0);
  });
});
