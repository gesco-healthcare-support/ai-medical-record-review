import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The heavy children + data hooks are stubbed so the test isolates the header's gating + banner
// logic (the core of this change). rowErrors stays REAL so invalid rows are computed genuinely.
vi.mock("@/hooks/use-review-workflow", () => ({ useReviewWorkflow: vi.fn() }));
vi.mock("@/hooks/use-summaries", () => ({ useSummaries: () => ({ data: [] }) }));
vi.mock("@/components/review/review-editor", () => ({ ReviewEditor: () => <div data-testid="editor" /> }));
vi.mock("@/components/review/summaries-view", () => ({ SummariesView: () => <div /> }));
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
    gotoStep: vi.fn(),
    ...over,
  } as unknown as ReturnType<typeof useReviewWorkflow>);
}

const summarize = () => screen.getByRole("button", { name: /Summarize/ });

describe("ReviewPageClient summarize gating", () => {
  it("lists each invalid row and disables Summarize", () => {
    mockWf({ rows: [row({ start: 1, end: 5 }), row({ start: 3, end: 7 })] }); // row 2 overlaps
    render(<ReviewPageClient documentId="d1" />);
    expect(screen.getByText(/Fix these before summarizing/i)).toBeInTheDocument();
    expect(screen.getByText(/Document 2: overlaps the previous document/i)).toBeInTheDocument();
    expect(summarize()).toBeDisabled();
  });

  it("disables Summarize when nothing is selected", () => {
    mockWf({ rows: [row({ include: false })] });
    render(<ReviewPageClient documentId="d1" />);
    expect(summarize()).toBeDisabled();
    expect(summarize()).toHaveAttribute("title", expect.stringMatching(/select at least one/i));
  });

  it("shows a persistent autosave-failure banner and blocks Summarize", () => {
    mockWf({ saveState: { kind: "error", message: "Not saved: couldn't reach the server." } });
    render(<ReviewPageClient documentId="d1" />);
    // The persistent banner (role=alert) is the loud surface; the header chip repeats it.
    expect(screen.getByRole("alert")).toHaveTextContent("Not saved: couldn't reach the server.");
    expect(summarize()).toBeDisabled();
  });

  it("enables Summarize when rows are valid, included, and saved", () => {
    mockWf({ saveState: { kind: "saved" } });
    render(<ReviewPageClient documentId="d1" />);
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
