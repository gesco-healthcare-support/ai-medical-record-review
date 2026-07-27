import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const resolveMock = vi.fn().mockResolvedValue({ ok: true });
const startDedupMock = vi.fn().mockResolvedValue({ ok: true });
const dupState: { data: unknown; error: unknown; isLoading: boolean } = {
  data: undefined,
  error: null,
  isLoading: false,
};
vi.mock("@/hooks/use-duplicates", () => ({
  useDuplicates: () => dupState,
  useResolveDuplicate: () => ({ mutateAsync: resolveMock, isPending: false }),
  useStartDedup: () => ({ mutateAsync: startDedupMock, isPending: false }),
}));

import { DuplicatesView } from "@/components/review/duplicates-view";

describe("DuplicatesView", () => {
  it("lists a cluster's copies and keeps one on click", async () => {
    dupState.error = null;
    dupState.data = {
      job: null,
      clusters: [
        {
          group: 1,
          dismissed: false,
          rows: [
            { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: false },
            { idx: 3, title: "Progress Note", date: "02/02/2026", pages: { start: 10, end: 11 }, include: true, primary: false },
          ],
        },
      ],
    };
    const onResolved = vi.fn();
    render(<DuplicatesView documentId="d1" onResolved={onResolved} />);

    expect(screen.getByRole("heading", { name: /Possible duplicate/i })).toBeInTheDocument();
    const keepButtons = screen.getAllByRole("button", { name: /keep this one/i });
    expect(keepButtons).toHaveLength(2);

    fireEvent.click(keepButtons[0]);
    await waitFor(() =>
      expect(resolveMock).toHaveBeenCalledWith({ group: 1, action: "keep_one", primaryIdx: 0 }),
    );
    await waitFor(() => expect(onResolved).toHaveBeenCalled());
  });

  it("shows the empty state when there are no clusters", () => {
    dupState.error = null;
    dupState.data = { job: null, clusters: [] };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText("No duplicates")).toBeInTheDocument();
  });

  it("offers a manual re-check when the clusters are stale", async () => {
    dupState.error = null;
    dupState.data = { job: null, clusters: [], stale: true };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/boundaries changed since the last duplicate check/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /re-check duplicates/i }));
    await waitFor(() => expect(startDedupMock).toHaveBeenCalled());
  });

  it("hides the re-check hint when not stale", () => {
    dupState.error = null;
    dupState.data = { job: null, clusters: [], stale: false };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByRole("button", { name: /re-check duplicates/i })).not.toBeInTheDocument();
  });

  it("hides the re-check hint while a dedup job is running", () => {
    dupState.error = null;
    dupState.data = { job: { state: "running", current: 3, total: 9 }, clusters: [], stale: true };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByRole("button", { name: /re-check duplicates/i })).not.toBeInTheDocument();
  });
});
