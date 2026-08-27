import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const resolveMock = vi.fn().mockResolvedValue({ ok: true });
const startDedupMock = vi.fn().mockResolvedValue({ ok: true });
const jumpTo = vi.fn();
// jsdom cannot run the pdf.js iframe; stub the viewer and record the page it is asked to show.
vi.mock("@/components/review/pdf-viewer", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    PdfViewer: forwardRef(function PdfViewerStub(_props: unknown, ref: unknown) {
      useImperativeHandle(ref as never, () => ({ jumpTo }), []);
      return <div data-testid="pdf-viewer" />;
    }),
  };
});
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

  it("opens a copy's first page in the viewer when its row or title is clicked", async () => {
    jumpTo.mockClear();
    resolveMock.mockClear();
    dupState.error = null;
    dupState.data = {
      job: null,
      clusters: [
        {
          group: 1,
          dismissed: false,
          rows: [
            { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 4, end: 6 }, include: true, primary: false },
            { idx: 3, title: "Progress Note", date: "02/02/2026", pages: { start: 10, end: 11 }, include: true, primary: false },
          ],
        },
      ],
    };
    render(<DuplicatesView documentId="d1" filename="record.pdf" />);
    expect(screen.getByTestId("pdf-viewer")).toBeInTheDocument();

    // Each copy's date/pages/title is one real button, so mouse and keyboard both reach the jump.
    fireEvent.click(screen.getByRole("button", { name: /pages 10.*Progress Note/ }));
    expect(jumpTo).toHaveBeenLastCalledWith(10);

    fireEvent.click(screen.getByText(/pages 4/));
    expect(jumpTo).toHaveBeenLastCalledWith(4);
    expect(resolveMock).not.toHaveBeenCalled();
  });

  it("keeps a copy without treating the click as a jump", async () => {
    jumpTo.mockClear();
    resolveMock.mockClear();
    dupState.error = null;
    dupState.data = {
      job: null,
      clusters: [
        {
          group: 2,
          dismissed: false,
          rows: [
            { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: false },
            { idx: 1, title: "Progress Note", date: "02/02/2026", pages: { start: 3, end: 4 }, include: true, primary: false },
          ],
        },
      ],
    };
    render(<DuplicatesView documentId="d1" />);
    fireEvent.click(screen.getAllByRole("button", { name: /keep this one/i })[0]);
    await waitFor(() => expect(resolveMock).toHaveBeenCalled());
    expect(jumpTo).not.toHaveBeenCalled();
  });

  it("reads a cluster as resolved once at most one copy is still included", () => {
    dupState.error = null;
    dupState.data = {
      job: null,
      clusters: [
        {
          group: 1,
          dismissed: false,
          rows: [
            { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: false },
            { idx: 3, title: "Progress Note", date: "02/02/2026", pages: { start: 10, end: 11 }, include: false, primary: false },
          ],
        },
      ],
    };
    render(<DuplicatesView documentId="d1" />);
    // No copy is marked "kept" (a re-check recomputed the group), but only one is still included.
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(screen.queryByText("Needs review")).not.toBeInTheDocument();
  });

  it("flags a cluster that still has two included copies", () => {
    dupState.error = null;
    dupState.data = {
      job: null,
      clusters: [
        {
          group: 1,
          dismissed: false,
          rows: [
            { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: true },
            { idx: 3, title: "Progress Note", date: "02/02/2026", pages: { start: 10, end: 11 }, include: true, primary: false },
          ],
        },
      ],
    };
    render(<DuplicatesView documentId="d1" />);
    // A primary is marked, but a second copy is included again -> the reviewer must look.
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("shows the empty state when a completed check found no clusters", () => {
    // `checked: true` is load-bearing. Without it this fixture describes a document nothing has looked
    // at, and "No duplicates" would be a false statement rather than an empty result.
    dupState.error = null;
    dupState.data = { job: null, clusters: [], checked: true };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText("No duplicates")).toBeInTheDocument();
  });

  it("hints at a manual re-check when the clusters are stale", () => {
    dupState.error = null;
    dupState.data = { job: null, clusters: [], stale: true, checked: true };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/boundaries changed since the last duplicate check/i)).toBeInTheDocument();
    // The re-check button lives in the page header (one per screen), not in this view.
    expect(screen.queryByRole("button", { name: /re-check duplicates/i })).not.toBeInTheDocument();
  });

  it("hides the re-check hint when not stale", () => {
    dupState.error = null;
    dupState.data = { job: null, clusters: [], stale: false, checked: true };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByText(/boundaries changed since the last duplicate check/i)).not.toBeInTheDocument();
  });

  it("hides the re-check hint while a dedup job is running", () => {
    dupState.error = null;
    dupState.data = {
      job: { state: "running", current: 3, total: 9 },
      clusters: [],
      stale: true,
      checked: true,
    };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByText(/boundaries changed since the last duplicate check/i)).not.toBeInTheDocument();
  });

  it("shows progress in the empty state, not just a static message", () => {
    // A 1498-page record measured ~47 minutes. Without a moving counter in the empty state - the
    // element that fills the tab while a check runs - a slow job reads as a hung one, which is
    // exactly how a reviewer reported it.
    dupState.error = null;
    dupState.data = { job: { state: "running", current: 137, total: 435 }, clusters: [] };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getAllByText(/Checking for duplicates \(137\/435\)/).length).toBeGreaterThan(1);
  });

  it("falls back to an ellipsis before the total is known", () => {
    dupState.error = null;
    dupState.data = { job: { state: "queued", current: 0, total: 0 }, clusters: [] };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getAllByText(/Checking for duplicates\.\.\./).length).toBeGreaterThan(0);
  });
});

describe("DuplicatesView per-copy removal", () => {
  const mixedCluster = {
    job: null,
    stale: false,
    unreadable: 0,
    clusters: [
      {
        group: 4,
        dismissed: false,
        similarity: 0.514,
        rows: [
          { idx: 0, title: "Work Status Report", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: false },
          { idx: 1, title: "Work Status Report", date: "02/02/2026", pages: { start: 3, end: 4 }, include: true, primary: false },
        ],
      },
    ],
  };

  it("drops one copy out of a mixed cluster once confirmed", async () => {
    // Real records produce 7-member groups spanning 7 dates: some copies belong and some do not, so
    // dismissing the whole group would throw away the genuine duplicates with the false ones.
    resolveMock.mockClear();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    dupState.error = null;
    dupState.data = mixedCluster;
    const onResolved = vi.fn();
    render(<DuplicatesView documentId="d1" onResolved={onResolved} />);

    fireEvent.click(screen.getAllByRole("button", { name: /not a duplicate/i })[1]);
    await waitFor(() =>
      expect(resolveMock).toHaveBeenCalledWith({ group: 4, action: "remove_member", idx: 1 }),
    );
    await waitFor(() => expect(onResolved).toHaveBeenCalled());
    confirm.mockRestore();
  });

  it("warns that a later re-check will ask about the removed copy again", () => {
    // Removals are keyed on the cluster's exact page-range set, so a re-check re-forms the group with
    // this copy back in it. The reviewer has to know that before pruning a seven-member cluster.
    resolveMock.mockClear();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    dupState.error = null;
    dupState.data = mixedCluster;
    render(<DuplicatesView documentId="d1" />);

    fireEvent.click(screen.getAllByRole("button", { name: /not a duplicate/i })[0]);
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/ask about it again/i));
    expect(resolveMock).not.toHaveBeenCalled(); // declining the warning changes nothing
    confirm.mockRestore();
  });

  it("offers no per-copy removal on a dismissed cluster", () => {
    dupState.error = null;
    dupState.data = { ...mixedCluster, clusters: [{ ...mixedCluster.clusters[0], dismissed: true }] };
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByRole("button", { name: /not a duplicate/i })).not.toBeInTheDocument();
  });
});

describe("DuplicatesView never checked", () => {
  // Empty clusters mean two different things and the tab presented both as "No duplicate documents
  // found": a completed check that found nothing, and no check at all. The second is the DEFAULT state
  // of every record, because duplicate detection only ever runs when someone asks for it.
  //
  // Measured 2026-08-19 on four records taken end to end: none had a dedup job, the tab reported no
  // duplicates on all four, and the human deliverables for two of them count 6 and 2 pages of
  // duplicate copies. The tab was affirmatively wrong, not merely silent.
  const neverChecked = (job: unknown = null) => ({
    job,
    stale: false,
    unreadable: 0,
    clusters: [],
    checked: false,
  });

  it("says the record has not been checked rather than that it is clean", () => {
    dupState.error = null;
    dupState.data = neverChecked();
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/no duplicate check has run on this record yet/i)).toBeInTheDocument();
    expect(screen.getAllByText("Not checked yet").length).toBeGreaterThan(0);
    // The claim that must NOT appear: it is the one a reviewer would act on.
    expect(screen.queryByText("No duplicates")).not.toBeInTheDocument();
    expect(screen.queryByText(/No duplicate documents found/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/has no groups of duplicate documents to review/i),
    ).not.toBeInTheDocument();
  });

  it("stays quiet while a check is running", () => {
    // Mid-run is not "never checked" - the running counter already says what is happening.
    dupState.error = null;
    dupState.data = neverChecked({ state: "running", current: 2, total: 9 });
    render(<DuplicatesView documentId="d1" />);
    expect(
      screen.queryByText(/no duplicate check has run on this record yet/i),
    ).not.toBeInTheDocument();
  });

  it("stays quiet before the payload has loaded", () => {
    // `undefined` data is "not known yet", which must not be reported as "not checked".
    dupState.error = null;
    dupState.data = undefined;
    render(<DuplicatesView documentId="d1" />);
    expect(
      screen.queryByText(/no duplicate check has run on this record yet/i),
    ).not.toBeInTheDocument();
  });
});

describe("DuplicatesView unreadable sub-documents", () => {
  const withUnreadable = (unreadable: number, job: unknown = null) => ({
    job,
    stale: false,
    unreadable,
    clusters: [],
    // A count of unreadable rows only exists once a check has completed, so these fixtures describe a
    // checked document. Without this they would also trip the never-checked banner and stop describing
    // the case they are named for.
    checked: true,
  });

  it("says how many sub-documents could not be read", () => {
    // Text-free rows match nothing, so they were never compared. Presenting that run as "No
    // duplicates" hands the reviewer a conclusion the check did not reach.
    dupState.error = null;
    dupState.data = withUnreadable(18);
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/18 sub-documents could not be read/i)).toBeInTheDocument();
    expect(screen.getByText(/were not compared/i)).toBeInTheDocument();
  });

  it("reads correctly for a single sub-document", () => {
    dupState.error = null;
    dupState.data = withUnreadable(1);
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/1 sub-document could not be read/i)).toBeInTheDocument();
    expect(screen.getByText(/was not compared/i)).toBeInTheDocument();
  });

  it("stays quiet when every sub-document was read", () => {
    dupState.error = null;
    dupState.data = withUnreadable(0);
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByText(/could not be read/i)).not.toBeInTheDocument();
  });

  it("stays quiet while the check is still running", () => {
    // Mid-run the count is a partial tally of rows not yet reached, which would read as a fault.
    dupState.error = null;
    dupState.data = withUnreadable(5, { state: "running", current: 2, total: 9 });
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByText(/could not be read/i)).not.toBeInTheDocument();
  });
});

describe("DuplicatesView similarity", () => {
  // Same two copies each time; only the stored score differs.
  const payload = (similarity: number | null) => ({
    job: null,
    stale: false,
    clusters: [
      {
        group: 1,
        dismissed: false,
        similarity,
        rows: [
          { idx: 0, title: "Progress Note", date: "01/02/2026", pages: { start: 1, end: 2 }, include: true, primary: false },
          { idx: 3, title: "Progress Note", date: "02/02/2026", pages: { start: 10, end: 11 }, include: true, primary: false },
        ],
      },
    ],
  });

  it("shows how alike the copies are as a percentage", () => {
    dupState.error = null;
    dupState.data = payload(0.974);
    render(<DuplicatesView documentId="d1" />);
    expect(screen.getByText(/97% of the text matches/i)).toBeInTheDocument();
  });

  it("says nothing for a cluster stored before the score was kept", () => {
    dupState.error = null;
    dupState.data = payload(null);
    render(<DuplicatesView documentId="d1" />);
    expect(screen.queryByText(/of the text matches/i)).not.toBeInTheDocument();
  });

  // A dedup job that errored or was interrupted used to fall into `neverChecked`, because the API
  // reports `checked: false` for it. So the counter reverted to "Not checked yet" mid-run with no
  // error text, `job.error` was fetched and read by nothing, and a failure was indistinguishable
  // from a check nobody had started. There is no other error path for dedup: the workflow hook does
  // not watch dedup jobs at all.
  it("says a duplicate check failed instead of pretending none ever ran", () => {
    dupState.error = null;
    dupState.data = {
      job: { state: "error", error: "OCR is unavailable on this host", current: 37, total: 84 },
      clusters: [],
      checked: false,
      stale: false,
      unreadable: 0,
    };
    render(<DuplicatesView documentId="d1" />);

    // Two places say so on purpose: the banner, and the empty state where the counter used to sit.
    expect(screen.getAllByText(/did not finish/i)).toHaveLength(2);
    expect(screen.getByText(/OCR is unavailable on this host/i)).toBeInTheDocument();
    // ...and must NOT claim the record has never been checked, or offer a clean bill of health.
    expect(screen.queryByText(/No duplicate check has run on this record yet/i)).toBeNull();
    expect(screen.queryByText(/No duplicates/i)).toBeNull();
  });

  it("keeps showing the last completed check's groups when a re-check fails", () => {
    dupState.error = null;
    dupState.data = {
      job: { state: "error", error: "boom", current: 0, total: 0 },
      checked: true,
      stale: false,
      unreadable: 0,
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
    render(<DuplicatesView documentId="d1" />);

    expect(screen.getByRole("heading", { name: /Possible duplicate/i })).toBeInTheDocument();
    expect(screen.getByText(/from the last check that completed/i)).toBeInTheDocument();
  });

  it("still says nothing has been compared when the only check that ran failed", () => {
    dupState.error = null;
    dupState.data = {
      job: { state: "interrupted", error: null, current: 0, total: 0 },
      clusters: [],
      checked: false,
      stale: false,
      unreadable: 0,
    };
    render(<DuplicatesView documentId="d1" />);

    expect(screen.getByText(/Nothing in this record has been compared yet/i)).toBeInTheDocument();
  });
});
