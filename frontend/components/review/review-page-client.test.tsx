import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The heavy children + data hooks are stubbed so the test isolates the header's gating + banner
// logic (the core of this change). rowErrors stays REAL so invalid rows are computed genuinely.
const dupState: { data: unknown } = { data: undefined };
const sumState: { data: unknown[] } = { data: [] };
const startDedupMock = vi.fn().mockResolvedValue({ ok: true });

vi.mock("@/hooks/use-review-workflow", () => ({ useReviewWorkflow: vi.fn() }));
vi.mock("@/hooks/use-summaries", () => ({ useSummaries: () => sumState }));
vi.mock("@/hooks/use-duplicates", () => ({
  useDuplicates: () => dupState,
  useStartDedup: () => ({ mutateAsync: startDedupMock, isPending: false }),
}));
vi.mock("@/components/review/review-editor", () => ({ ReviewEditor: () => <div data-testid="editor" /> }));
vi.mock("@/components/review/summaries-view", () => ({ SummariesView: () => <div /> }));
vi.mock("@/components/review/duplicates-view", () => ({ DuplicatesView: () => <div /> }));
vi.mock("@/components/review/header-bar", () => ({ HeaderBar: () => <div /> }));
vi.mock("@/components/review/start-panel", () => ({ StartPanel: () => <div /> }));
vi.mock("@/components/review/progress-panel", () => ({ ProgressPanel: () => <div /> }));

import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { ReviewPageClient } from "@/components/review/review-page-client";
import type { EditorRow } from "@/lib/review-rows";

const row = (over: Partial<EditorRow>): EditorRow => ({
  start: 1,
  end: 3,
  category: "1",
  title: "",
  date: "",
  injury_date: "",
  flag: "-",
  suggest_merge: false,
  include: true,
  _key: `k${Math.random()}`,
  ...over,
});

function mockWf(over: Record<string, unknown>) {
  vi.mocked(useReviewWorkflow).mockReturnValue({
    section: "editor",
    activeStep: "review",
    rows: [row({})],
    categories: [],
    totalPages: 10,
    filename: "f.pdf",
    banner: "",
    setBanner: vi.fn(),
    watching: false,
    startHint: "",
    progress: { title: "", pct: 0, detail: "" },
    saveState: { kind: "" },
    header: null,
    setHeader: vi.fn(),
    attention: null,
    onStart: vi.fn(),
    onSummarize: vi.fn(),
    onRowsChange: vi.fn(),
    reloadRows: vi.fn(),
    gotoStep: vi.fn(),
    ...over,
  } as unknown as ReturnType<typeof useReviewWorkflow>);
}

const button = (name: RegExp) => screen.getByRole("button", { name });
const maybeButton = (name: RegExp) => screen.queryByRole("button", { name });
const summarize = () => button(/^Summarize/);
/** Reach the Duplicates step. Uses the TAB, not the "Check duplicates" button: since 2026-08-06 that
 *  button STARTS a dedup job, which would add a call these tests are not asking about - and it is
 *  disabled while a check runs, so a button-based helper could not reach the tab in the state one of
 *  these tests needs. Clicking the tab is also what a reviewer does on the way back. */
const gotoDuplicates = () => fireEvent.click(screen.getByRole("tab", { name: /Duplicates/ }));

beforeEach(() => {
  dupState.data = undefined;
  sumState.data = [];
  startDedupMock.mockClear();
});

// vi.spyOn(window, "confirm") is used by several tests below and was never restored, so a later spy
// inherited earlier calls and confirm.mock.calls[0] could belong to a different test.
afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReviewPageClient unidentified count", () => {
  // The tab carries the count so the size of the manual check the reviewers asked for is visible
  // from Duplicates and Summaries too, not only from the tab that holds the filter (issue #144).
  it("shows the count on the Review tab when documents could not be identified", () => {
    mockWf({
      rows: [
        row({ category: "100", ruled_paperwork: false }),
        row({ category: "100", ruled_paperwork: true }), // a rule named it: not part of the check
        row({ category: "5" }),
      ],
    });
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByRole("tab", { name: /Review & correct . 1/ })).toBeInTheDocument();
  });

  it("shows the bare label when every document was identified", () => {
    mockWf({ rows: [row({ category: "100", ruled_paperwork: true }), row({ category: "5" })] });
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByRole("tab", { name: "Review & correct" })).toBeInTheDocument();
  });
});

describe("ReviewPageClient step-flow actions", () => {
  it("offers only the review step's actions on Review & correct", () => {
    sumState.data = [{ idx: 0 }];
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    expect(button(/Re-run segment/)).toBeInTheDocument();
    expect(button(/Check duplicates/)).toBeInTheDocument();
    expect(maybeButton(/^Summarize/)).not.toBeInTheDocument();
    expect(maybeButton(/Re-summarize all/)).not.toBeInTheDocument();
  });

  it("starts the duplicate check and then switches to the Duplicates step", async () => {
    // Inverted 2026-08-06: this used to assert the button navigated WITHOUT starting a check, back
    // when dedup ran automatically after identify. The reviewer now starts it here, once they have
    // chosen which sub-documents to summarize.
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    fireEvent.click(button(/Check duplicates/));
    await screen.findByRole("button", { name: /Re-check duplicates/ });
    expect(startDedupMock).toHaveBeenCalledTimes(1);
    expect(summarize()).toBeInTheDocument();
    expect(maybeButton(/Re-run segment/)).not.toBeInTheDocument();
  });

  it("does not start a check while there are unsaved edits", () => {
    // Dedup reads include=True server-side. Scanning against unsaved checkbox changes would check
    // the wrong rows - precisely the waste this gate exists to prevent.
    mockWf({ saveState: { kind: "dirty" } });
    render(<ReviewPageClient documentId="d1" />);
    const btn = button(/Check duplicates/);
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Your latest changes aren't saved yet.");
    fireEvent.click(btn);
    expect(startDedupMock).not.toHaveBeenCalled();
  });

  it("banners a failure to start and stays on the Review step", async () => {
    const setBanner = vi.fn();
    startDedupMock.mockRejectedValueOnce(new Error("redis down"));
    mockWf({ setBanner });
    render(<ReviewPageClient documentId="d1" />);
    fireEvent.click(button(/Check duplicates/));
    await waitFor(() => expect(setBanner).toHaveBeenCalledWith(expect.stringMatching(/\S/)));
    // Still on Review: the banner belongs where the reviewer is, not on a tab they never reached.
    expect(button(/Re-run segment/)).toBeInTheDocument();
    expect(maybeButton(/Re-check duplicates/)).not.toBeInTheDocument();
  });

  it("starts a manual re-check from the Duplicates step", async () => {
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    fireEvent.click(button(/Re-check duplicates/));
    // Nothing on screen to lose (no clusters yet), so the first check asks nothing.
    await waitFor(() => expect(startDedupMock).toHaveBeenCalledTimes(1));
  });

  it("warns before a re-check discards per-copy curation", async () => {
    // A re-check reclusters from scratch and only re-applies a dismissal to a cluster holding exactly
    // the same copies, so copies the reviewer removed one at a time come back.
    dupState.data = { clusters: [{ group: 1, dismissed: false, rows: [] }], job: null, stale: false };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    fireEvent.click(button(/Re-check duplicates/));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/asked about again/i));
    await waitFor(() => expect(startDedupMock).toHaveBeenCalledTimes(1));
    confirm.mockRestore();
  });

  it("does not re-check when the warning is declined", () => {
    dupState.data = { clusters: [{ group: 1, dismissed: false, rows: [] }], job: null, stale: false };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    fireEvent.click(button(/Re-check duplicates/));
    expect(startDedupMock).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("disables both Duplicates actions while a check is running", () => {
    dupState.data = { clusters: [], job: { state: "running", current: 1, total: 4 }, stale: false };
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(button(/Re-check duplicates/)).toBeDisabled();
    expect(summarize()).toBeDisabled();
    expect(summarize()).toHaveAttribute("title", expect.stringMatching(/duplicate check/i));
  });

  it("offers Re-summarize all on the Summaries step only", () => {
    sumState.data = [{ idx: 0 }];
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    fireEvent.click(screen.getByRole("tab", { name: /Summaries/ }));
    expect(button(/Re-summarize all/)).toBeInTheDocument();
    expect(maybeButton(/Re-run segment/)).not.toBeInTheDocument();
    expect(maybeButton(/^Summarize \d/)).not.toBeInTheDocument();
  });

  it("spells out that the full re-run regenerates everything with the current prompts", () => {
    sumState.data = [{ idx: 0 }, { idx: 1 }];
    const onSummarize = vi.fn();
    mockWf({ onSummarize });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ReviewPageClient documentId="d1" />);
    fireEvent.click(screen.getByRole("tab", { name: /Summaries/ }));

    const control = button(/Re-summarize all/);
    expect(control).toHaveAttribute("title", expect.stringMatching(/current prompts/i));
    fireEvent.click(control);

    const message = confirm.mock.calls[0][0] as string;
    expect(message).toMatch(/all 2 summaries/i);
    expect(message).toMatch(/from scratch/i);
    expect(message).toMatch(/current prompts/i);
    expect(message).toMatch(/discarded/i);
    expect(onSummarize).toHaveBeenCalledWith(true); // fresh=true, so the worker deletes first
    confirm.mockRestore();
  });
});

describe("ReviewPageClient duplicate advisory count", () => {
  const cluster = (includes: boolean[], dismissed = false) => ({
    group: 1,
    dismissed,
    rows: includes.map((include, idx) => ({
      idx,
      title: "T",
      date: "-",
      pages: { start: idx + 1, end: idx + 1 },
      include,
      primary: false,
    })),
  });

  it("advises a cluster while two copies would still be summarized", () => {
    dupState.data = { clusters: [cluster([true, true])], job: null, stale: false };
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByText(/1 possible duplicate group to review/i)).toBeInTheDocument();
  });

  it("stops advising once only one copy is included", () => {
    dupState.data = { clusters: [cluster([true, false])], job: null, stale: false };
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.queryByText(/possible duplicate/i)).not.toBeInTheDocument();
  });
});

describe("ReviewPageClient summarize gating", () => {
  it("lists each invalid row and disables Summarize", () => {
    mockWf({ rows: [row({ start: 1, end: 5 }), row({ start: 3, end: 7 })] }); // row 2 overlaps
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByText(/Fix these before summarizing/i)).toBeInTheDocument();
    expect(screen.getByText(/Document 2: overlaps the previous document/i)).toBeInTheDocument();
    gotoDuplicates();
    expect(summarize()).toBeDisabled();
  });

  it("disables Summarize when nothing is selected", () => {
    mockWf({ rows: [row({ include: false })] });
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(summarize()).toBeDisabled();
    expect(summarize()).toHaveAttribute("title", expect.stringMatching(/select at least one/i));
  });

  it("shows a persistent autosave-failure banner and blocks Summarize", () => {
    mockWf({ saveState: { kind: "error", message: "Not saved: couldn't reach the server." } });
    render(<ReviewPageClient documentId="d1" />);
    // The persistent banner (role=alert) is the loud surface; the header chip repeats it.
    expect(screen.getByRole("alert")).toHaveTextContent("Not saved: couldn't reach the server.");
    gotoDuplicates();
    expect(summarize()).toBeDisabled();
  });

  it("enables Summarize when rows are valid, included, and saved", () => {
    mockWf({ saveState: { kind: "saved" } });
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(summarize()).toBeEnabled();
  });
});

describe("ReviewPageClient needs-attention notice", () => {
  it("lists each sub-document that could not be summarized, with page range, title, and reason", () => {
    mockWf({
      rows: [row({ start: 5, end: 5, title: "Laboratory Report" })],
      attention: {
        message: "1 of 2 documents could not be summarized.",
        rows: [{ idx: 0, pages: "5-5", reason: "No readable text was found in this document." }],
      },
    });
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByText(/1 of 2 documents could not be summarized/i)).toBeInTheDocument();
    expect(screen.getByText(/Pages 5-5 - Laboratory Report:/i)).toBeInTheDocument();
    expect(screen.getByText(/No readable text was found in this document\./i)).toBeInTheDocument();
  });
});

describe("ReviewPageClient blocking reasons follow the Summarize button", () => {
  const gotoSummaries = () => fireEvent.click(screen.getByRole("tab", { name: /Summaries/ }));

  it("lists the invalid page ranges on the step that holds Summarize", () => {
    mockWf({ rows: [row({ start: 1, end: 5 }), row({ start: 3, end: 7 })] }); // row 2 overlaps
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(screen.getByText(/Fix these before summarizing/i)).toBeInTheDocument();
    expect(screen.getByText(/Document 2: overlaps the previous document/i)).toBeInTheDocument();
    expect(summarize()).toBeDisabled();
  });

  it("repeats an autosave failure on the step that holds Summarize", () => {
    mockWf({ saveState: { kind: "error", message: "Not saved: couldn't reach the server." } });
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(screen.getByRole("alert")).toHaveTextContent("Not saved: couldn't reach the server.");
  });

  it("keeps both banners off Summaries, which has no Summarize button", () => {
    mockWf({
      rows: [row({ start: 1, end: 5 }), row({ start: 3, end: 7 })],
      saveState: { kind: "error", message: "Not saved: couldn't reach the server." },
    });
    render(<ReviewPageClient documentId="d1" />);
    gotoSummaries();
    expect(screen.queryByText(/Fix these before summarizing/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ReviewPageClient re-segment gating", () => {
  it("disables the segment button while a duplicate check runs", () => {
    dupState.data = { clusters: [], job: { state: "running", current: 1, total: 4 }, stale: false };
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    const segment = button(/Re-run segment/);
    expect(segment).toBeDisabled();
    expect(segment).toHaveAttribute("title", expect.stringMatching(/duplicate check/i));
  });

  it("leaves the segment button available when no check is running", () => {
    dupState.data = { clusters: [], job: null, stale: false };
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    expect(button(/Re-run segment/)).toBeEnabled();
  });
});

// Stop is two-stage: cooperative first, escalating to a hard kill only after the SERVER's grace
// period. The escalation is a timer, and a timer that outlives its run is the failure mode worth
// pinning - a force stop can land mid-transaction, so it must never be what the FIRST press does.
describe("ReviewPageClient stop escalation", () => {
  const stop = (c: HTMLElement) => c.querySelector(".rce-stop") as HTMLButtonElement;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("offers Force stop only once the grace period has passed", async () => {
    const cancelActiveJob = vi.fn().mockResolvedValue(10);
    mockWf({ watching: true, cancelActiveJob });
    const { container } = render(<ReviewPageClient documentId="d1" />);

    expect(stop(container).textContent).toBe("Stop");

    await act(async () => {
      fireEvent.click(stop(container));
    });
    // Cooperative: the reviewer is told it was asked to stop, not offered a kill yet.
    expect(stop(container).textContent).toBe("Stopping...");
    expect(cancelActiveJob).toHaveBeenCalledWith(false);

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(stop(container).textContent).toBe("Force stop");
  });

  it("does not carry a pending escalation into the next run", async () => {
    // The regression: the escalation timer is scheduled on the first Stop. When the run ends BEFORE
    // it fires, an uncleared timer lands after the reset and leaves the button stuck on "Force stop",
    // so the reviewer's first press on the NEXT job is silently a hard kill.
    const cancelActiveJob = vi.fn().mockResolvedValue(10);
    mockWf({ watching: true, cancelActiveJob });
    const { container, rerender } = render(<ReviewPageClient documentId="d1" />);

    await act(async () => {
      fireEvent.click(stop(container));
    });

    // It stops cooperatively at 4s - comfortably inside the 10s grace.
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    mockWf({ watching: false, cancelActiveJob, cancelledJob: { kind: "segment" } });
    rerender(<ReviewPageClient documentId="d1" />);

    // The original deadline passes while nothing is running.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    // A new run starts on the same page.
    mockWf({ watching: true, cancelActiveJob });
    rerender(<ReviewPageClient documentId="d1" />);

    expect(stop(container).textContent).toBe("Stop");
  });

  it("does not carry an escalation across a chained job while it keeps watching", async () => {
    // The live regression, and the one the `watching`-only reset could not catch: segmentation chains
    // into the duplicate check, so the active job changes WITHOUT `watching` ever going false. The
    // grace period legitimately expired on the finished job; the chained job then rendered "Force
    // stop" as its first state, making the reviewer's first press a hard kill on a run that had never
    // been asked to stop cooperatively.
    const cancelActiveJob = vi.fn().mockResolvedValue(10);
    mockWf({ watching: true, activeJobId: 1, cancelActiveJob });
    const { container, rerender } = render(<ReviewPageClient documentId="d1" />);

    await act(async () => {
      fireEvent.click(stop(container));
    });
    // This job never acknowledges, so escalating is CORRECT for it.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(stop(container).textContent).toBe("Force stop");

    // The chain moves on: a different job, still watching, bar never gone.
    mockWf({ watching: true, activeJobId: 2, cancelActiveJob });
    rerender(<ReviewPageClient documentId="d1" />);

    expect(stop(container).textContent).toBe("Stop");

    // And the first press on it is cooperative, not a kill.
    await act(async () => {
      fireEvent.click(stop(container));
    });
    expect(cancelActiveJob).toHaveBeenLastCalledWith(false);
  });
});
