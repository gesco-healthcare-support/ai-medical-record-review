import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
/** Summarize lives on the Duplicates step now, so gating tests go through the gate. */
const gotoDuplicates = () => fireEvent.click(button(/Check duplicates/));

beforeEach(() => {
  dupState.data = undefined;
  sumState.data = [];
  startDedupMock.mockClear();
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

  it("switches to the Duplicates step without starting a check", () => {
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    expect(button(/Re-check duplicates/)).toBeInTheDocument();
    expect(summarize()).toBeInTheDocument();
    expect(maybeButton(/Re-run segment/)).not.toBeInTheDocument();
    expect(startDedupMock).not.toHaveBeenCalled();
  });

  it("starts a manual re-check from the Duplicates step", async () => {
    mockWf({});
    render(<ReviewPageClient documentId="d1" />);
    gotoDuplicates();
    fireEvent.click(button(/Re-check duplicates/));
    await waitFor(() => expect(startDedupMock).toHaveBeenCalledTimes(1));
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
