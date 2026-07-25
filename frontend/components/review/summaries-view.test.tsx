import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Mutable holder so the mock factory (hoisted) can read a value the test sets before render.
const summariesState: { data: unknown; error: unknown; isLoading: boolean } = {
  data: undefined,
  error: null,
  isLoading: false,
};
vi.mock("@/hooks/use-summaries", () => ({
  useSummaries: () => summariesState,
  useSaveSummary: () => ({ mutateAsync: vi.fn() }),
  useResummarize: () => ({ mutateAsync: vi.fn() }),
}));

import { ApiError } from "@/lib/api";
import { SummariesView } from "@/components/review/summaries-view";

describe("SummariesView error handling", () => {
  it("shows a humanized message when the summaries fail to load", () => {
    summariesState.error = new ApiError("network", 0);
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoReview={vi.fn()} />,
    );
    expect(screen.getByText(/couldn't reach the server/i)).toBeInTheDocument();
  });
});

describe("SummariesView verify flag", () => {
  it("shows the AI-fixed flag when the verify pass corrected a summary", () => {
    summariesState.error = null;
    summariesState.isLoading = false;
    summariesState.data = [
      {
        idx: 0,
        summaryTitle: "Progress Note (Pages 1-1)",
        summaryDate: "01/02/2026",
        summaryText: "Body.",
        manualCheck: false,
        excluded: false,
        edited: false,
        verified: true,
        verifyChanged: true,
        verifyIssues: [{ type: "unsupported", detail: "x" }],
        row: { start: 1, end: 1, category: "1" },
      },
    ];
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoReview={vi.fn()} />,
    );
    expect(screen.getByText(/AI-fixed/i)).toBeInTheDocument();
  });
});
