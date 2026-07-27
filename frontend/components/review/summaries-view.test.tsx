import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Mutable holder so the mock factory (hoisted) can read a value the test sets before render.
const summariesState: { data: unknown; error: unknown; isLoading: boolean } = {
  data: undefined,
  error: null,
  isLoading: false,
};
const saveMock = vi.fn().mockResolvedValue({});
vi.mock("@/hooks/use-summaries", () => ({
  useSummaries: () => summariesState,
  useSaveSummary: () => ({ mutateAsync: saveMock }),
  useResummarize: () => ({ mutateAsync: vi.fn() }),
}));
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

import { fireEvent } from "@testing-library/react";
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

describe("SummariesView source pages", () => {
  const summary = (over: Record<string, unknown> = {}) => ({
    idx: 0,
    summaryTitle: "Progress Note (Pages 12-14)",
    summaryDate: "01/02/2026",
    summaryText: "Body.",
    manualCheck: false,
    excluded: false,
    edited: false,
    verified: false,
    verifyChanged: false,
    verifyIssues: [],
    row: { start: 12, end: 14, category: "1" },
    ...over,
  });

  it("jumps the viewer to the summary's first source page when its card is clicked", () => {
    jumpTo.mockClear();
    summariesState.error = null;
    summariesState.data = [summary()];
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoReview={vi.fn()} />,
    );
    expect(screen.getByTestId("pdf-viewer")).toBeInTheDocument();

    // Both the title and the meta line are buttons, so either one (mouse or keyboard) jumps.
    fireEvent.click(screen.getByRole("button", { name: /Progress Note/ }));
    expect(jumpTo).toHaveBeenLastCalledWith(12);

    jumpTo.mockClear();
    fireEvent.click(screen.getByText(/pages 12/));
    expect(jumpTo).toHaveBeenLastCalledWith(12);
  });

  it("does not jump when a card action is used", () => {
    jumpTo.mockClear();
    saveMock.mockClear();
    summariesState.error = null;
    summariesState.data = [summary()];
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoReview={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    expect(jumpTo).not.toHaveBeenCalled();

    // The card is in edit mode now: typing in it must not move the viewer either.
    fireEvent.click(screen.getByRole("textbox", { name: /Summary text/i }));
    expect(jumpTo).not.toHaveBeenCalled();
  });
});
