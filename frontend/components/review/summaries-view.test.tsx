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

import { fireEvent, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { SummariesView } from "@/components/review/summaries-view";

describe("SummariesView error handling", () => {
  it("shows a humanized message when the summaries fail to load", () => {
    summariesState.error = new ApiError("network", 0);
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
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
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/AI-fixed/i)).toBeInTheDocument();
  });
});

describe("SummariesView category", () => {
  const CATS = [
    { id: "1", name: "Treating progress and follow-up reports (PR-2)" },
    { id: "3", name: "Diagnostic studies" },
  ];
  /** One summary generated under category `generated`, whose row now says `live`. */
  const card = (generated: string, live: string | null | undefined) => [
    {
      idx: 0,
      summaryTitle: "Progress Note (Pages 1-2)",
      summaryDate: "01/02/2026",
      summaryText: "Body.",
      manualCheck: false,
      excluded: false,
      edited: false,
      verified: false,
      verifyChanged: false,
      verifyIssues: [],
      row: { start: 1, end: 2, category: generated },
      ...(live === undefined ? {} : { rowCategoryLive: live }),
    },
  ];

  const renderCard = () =>
    render(
      <SummariesView documentId="d1" categories={CATS} header={null} onGotoSummarizeStep={vi.fn()} />,
    );

  it("saves a new category without touching the summary text", async () => {
    saveMock.mockClear();
    summariesState.error = null;
    summariesState.data = card("1", "1");
    renderCard();

    fireEvent.change(screen.getByRole("combobox", { name: /document category/i }), {
      target: { value: "3" },
    });

    await waitFor(() => expect(saveMock).toHaveBeenCalledWith({ idx: 0, body: { category: "3" } }));
    // The body is deliberately NOT re-written here - only a re-draft rewrites the text.
    expect(screen.getByText("Body.")).toBeInTheDocument();
  });

  it("flags a summary whose row was re-classified after it was written", () => {
    summariesState.error = null;
    summariesState.data = card("1", "3");
    renderCard();
    expect(screen.getByText(/Category changed - re-draft to apply/i)).toBeInTheDocument();
  });

  it("stays quiet while the row still carries the generating category", () => {
    summariesState.error = null;
    summariesState.data = card("1", "1");
    renderCard();
    expect(screen.queryByText(/Category changed/i)).not.toBeInTheDocument();
  });

  it("stays quiet when no row covers the summary's pages any more", () => {
    // null = the boundaries were re-segmented. There is no live category to disagree with, so
    // claiming a mismatch would send the reviewer to re-draft for no reason.
    summariesState.error = null;
    summariesState.data = card("1", null);
    renderCard();
    expect(screen.queryByText(/Category changed/i)).not.toBeInTheDocument();
  });

  it("stays quiet when the field is missing entirely", () => {
    // An older backend serves no rowCategoryLive at all. `undefined !== null` is TRUE, so a plain
    // null check reported EVERY card as stale mid-deploy; the value is coalesced instead.
    summariesState.error = null;
    summariesState.data = card("1", undefined);
    renderCard();
    expect(screen.queryByText(/Category changed/i)).not.toBeInTheDocument();
  });

  it("flags a summary whose sub-document no longer exists", () => {
    // The reviewer merged or re-spanned the row AFTER this text was written, so it describes pages
    // nothing claims - and it still exports, because the export filters on `excluded` alone.
    summariesState.error = null;
    summariesState.data = [{ ...card("1", null)[0], rowMissing: true }];
    renderCard();
    expect(screen.getByText(/Pages changed - re-summarize/i)).toBeInTheDocument();
  });

  it("stays quiet for a summary whose sub-document is still there", () => {
    summariesState.error = null;
    summariesState.data = [{ ...card("1", "1")[0], rowMissing: false }];
    renderCard();
    expect(screen.queryByText(/Pages changed/i)).not.toBeInTheDocument();
  });

  it("stays quiet when the backend sends no rowMissing at all", () => {
    // The same rolling-deploy trap the rowCategoryLive coalescing exists for: an older backend
    // omits the field, and a badge on every card would be worse than no badge.
    summariesState.error = null;
    summariesState.data = card("1", null);
    renderCard();
    expect(screen.queryByText(/Pages changed/i)).not.toBeInTheDocument();
  });

  it("flags a summary written under a category the cascade guessed", () => {
    // Adam, 2026-08-31: "an extra tag to show that it wasn't confident would be useful". This tab
    // is where it matters most - reading a summary against its source pages happens from here, and
    // that is how the EMG report written up with the evaluation checklist was caught.
    summariesState.error = null;
    summariesState.data = [{ ...card("13", "13")[0], rowMethodLive: "llm-disagree" }];
    renderCard();
    expect(screen.getByText(/Category guessed/i)).toBeInTheDocument();
  });

  it("stays quiet when a rule decided the row's category", () => {
    summariesState.error = null;
    summariesState.data = [{ ...card("1", "1")[0], rowMethodLive: "rules" }];
    renderCard();
    expect(screen.queryByText(/Category guessed/i)).not.toBeInTheDocument();
  });

  it("stays quiet when both classifiers agreed", () => {
    summariesState.error = null;
    summariesState.data = [{ ...card("1", "1")[0], rowMethodLive: "llm+embedding" }];
    renderCard();
    expect(screen.queryByText(/Category guessed/i)).not.toBeInTheDocument();
  });

  it("stays quiet when the backend sends no rowMethodLive", () => {
    // Same rolling-deploy trap as rowMissing: an older backend omits the field, and a tag on every
    // card would be worse than no tag.
    summariesState.error = null;
    summariesState.data = card("1", "1");
    renderCard();
    expect(screen.queryByText(/Category guessed/i)).not.toBeInTheDocument();
  });

  it("reads the LIVE category, so a re-classify out of General is respected", () => {
    // categoryWasGuessed leaves General to couldNotIdentify. The summary was generated at 100 and
    // the row now says 5, so the tag has to key on the live value or it stays silent on exactly the
    // row a reviewer just moved into the deliverable.
    summariesState.error = null;
    summariesState.data = [{ ...card("100", "5")[0], rowMethodLive: "llm-disagree" }];
    renderCard();
    expect(screen.getByText(/Category guessed/i)).toBeInTheDocument();
  });

  it("shows the live category in the select, not the generating snapshot", () => {
    summariesState.error = null;
    summariesState.data = card("1", "3");
    renderCard();
    expect(screen.getByRole("combobox", { name: /document category/i })).toHaveValue("3");
  });

  it("reports a refused category change instead of silently keeping it", async () => {
    // The server returns 409 while any job runs, because a segment job would overwrite the row.
    saveMock.mockClear();
    saveMock.mockRejectedValueOnce(new ApiError("a job is running for this document", 409));
    summariesState.error = null;
    summariesState.data = card("1", "1");
    renderCard();

    fireEvent.change(screen.getByRole("combobox", { name: /document category/i }), {
      target: { value: "3" },
    });

    await waitFor(() =>
      expect(screen.getByText(/Category not saved/i)).toBeInTheDocument(),
    );
    saveMock.mockResolvedValue({});
  });

  it("re-pulls the editor's rows so a later edit cannot revert the category", async () => {
    // Review & correct renders from an in-memory buffer and autosaves the WHOLE row set. Leaving it
    // stale after a category write means the reviewer's next edit there sends the old category back
    // and silently undoes this change - found live, not by a mock.
    saveMock.mockClear();
    const onRowsChanged = vi.fn();
    summariesState.error = null;
    summariesState.data = card("1", "1");
    render(
      <SummariesView
        documentId="d1"
        categories={CATS}
        header={null}
        onGotoSummarizeStep={vi.fn()}
        onRowsChanged={onRowsChanged}
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: /document category/i }), {
      target: { value: "3" },
    });

    await waitFor(() => expect(onRowsChanged).toHaveBeenCalledTimes(1));
  });

  it("does not re-pull the rows when the category save failed", async () => {
    saveMock.mockClear();
    saveMock.mockRejectedValueOnce(new ApiError("a job is running", 409));
    const onRowsChanged = vi.fn();
    summariesState.error = null;
    summariesState.data = card("1", "1");
    render(
      <SummariesView
        documentId="d1"
        categories={CATS}
        header={null}
        onGotoSummarizeStep={vi.fn()}
        onRowsChanged={onRowsChanged}
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: /document category/i }), {
      target: { value: "3" },
    });

    await waitFor(() => expect(screen.getByText(/Category not saved/i)).toBeInTheDocument());
    expect(onRowsChanged).not.toHaveBeenCalled(); // nothing changed server-side, nothing to re-pull
    saveMock.mockResolvedValue({});
  });

  it("clears the badge once the re-draft catches the snapshot up", async () => {
    // The transition a reviewer actually performs, which no other test covered: badge visible, then
    // re-draft, then gone. resummarize re-snapshots row_category from the live row, so its response has
    // the two fields equal - this asserts the component reads that as "no longer stale" rather than
    // needing a full refetch. Proven at the API level in
    // test_redraft_after_a_category_change_uses_the_new_categorys_prompt; this is the render half.
    summariesState.error = null;
    summariesState.data = card("1", "3"); // generated as 1, row now says 3
    const { rerender } = render(
      <SummariesView documentId="d1" categories={CATS} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/Category changed - re-draft to apply/i)).toBeInTheDocument();

    // What the server returns after the re-draft: the snapshot now equals the row.
    summariesState.data = card("3", "3");
    rerender(
      <SummariesView documentId="d1" categories={CATS} header={null} onGotoSummarizeStep={vi.fn()} />,
    );

    expect(screen.queryByText(/Category changed/i)).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /document category/i })).toHaveValue("3");
  });

  it("does not jump the viewer when the category select is used", () => {
    jumpTo.mockClear();
    saveMock.mockClear();
    summariesState.error = null;
    summariesState.data = card("1", "1");
    renderCard();

    fireEvent.click(screen.getByRole("combobox", { name: /document category/i }));
    expect(jumpTo).not.toHaveBeenCalled();
  });
});

describe("SummariesView date of injury", () => {
  const withText = (summaryText: string) => [
    {
      idx: 0,
      summaryTitle: "Progress Note (Pages 1-2)",
      summaryDate: "01/02/2026",
      summaryText,
      manualCheck: false,
      excluded: false,
      edited: false,
      verified: false,
      verifyChanged: false,
      verifyIssues: [],
      row: { start: 1, end: 2, category: "1" },
    },
  ];

  it("shows every injury date a legacy stored summary stated, and none of the prefix in the body", () => {
    // 709 summaries predate the house grammar; their comma-terminated prefix must still parse.
    summariesState.error = null;
    summariesState.data = withText("**DOI**:05/08/2022, 06/01/2023, Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/DOI 05\/08\/2022, 06\/01\/2023/)).toBeInTheDocument();
    expect(screen.getByText("Lumbar strain noted.")).toBeInTheDocument();
    expect(screen.queryByText(/\*\*DOI\*\*/)).not.toBeInTheDocument();
  });

  it("shows every injury date in the house grammar", () => {
    summariesState.error = null;
    summariesState.data = withText("**DOI**: 05/08/22 & 06/01/23. Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/DOI 05\/08\/22 & 06\/01\/23/)).toBeInTheDocument();
    expect(screen.getByText("Lumbar strain noted.")).toBeInTheDocument();
    expect(screen.queryByText(/\*\*DOI\*\*/)).not.toBeInTheDocument();
  });

  it("keeps a cumulative-trauma period as one value", () => {
    summariesState.error = null;
    summariesState.data = withText("**DOI**: CT 01/02/20-03/04/21. Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/DOI CT 01\/02\/20-03\/04\/21/)).toBeInTheDocument();
    expect(screen.getByText("Lumbar strain noted.")).toBeInTheDocument();
  });

  it("shows both dates of a comma-joined value, and leaves neither in the body", () => {
    // DEMONSTRATES the bug. The multi-DOI shape both injury_date columns document is
    // "MM/DD/YYYY, MM/DD/YYYY", and the review page's injury-date cell is free text stored
    // verbatim - but items could only be joined by "&", so this failed the house pattern and fell
    // through to the legacy one, which requires a trailing comma and backtracks to the FIRST.
    // The chip showed 05/08/2022 alone and "06/01/2023." became the opening words of the body.
    summariesState.error = null;
    summariesState.data = withText("**DOI**: 05/08/2022, 06/01/2023. Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/DOI 05\/08\/2022, 06\/01\/2023/)).toBeInTheDocument();
    expect(screen.getByText("Lumbar strain noted.")).toBeInTheDocument();
    expect(screen.queryByText(/06\/01\/2023\. Lumbar/)).not.toBeInTheDocument();
  });

  it("reads a cumulative-trauma period written with a colon", () => {
    // "CT:" matched neither pattern - the house one could not consume the colon, the legacy one
    // needs a digit straight after "**DOI**:" - so the whole prefix stayed in the body.
    summariesState.error = null;
    summariesState.data = withText("**DOI**: CT: 01/02/20-03/04/21. Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.getByText(/DOI CT: 01\/02\/20-03\/04\/21/)).toBeInTheDocument();
    expect(screen.getByText("Lumbar strain noted.")).toBeInTheDocument();
    expect(screen.queryByText(/\*\*DOI\*\*/)).not.toBeInTheDocument();
  });

  it("shows no date when the summary states none", () => {
    summariesState.error = null;
    summariesState.data = withText("Lumbar strain noted.");
    render(
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    expect(screen.queryByText(/DOI /)).not.toBeInTheDocument();
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
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
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
      <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    expect(jumpTo).not.toHaveBeenCalled();

    // The card is in edit mode now: typing in it must not move the viewer either.
    fireEvent.click(screen.getByRole("textbox", { name: /Summary text/i }));
    expect(jumpTo).not.toHaveBeenCalled();
  });
});

describe("SummariesView empty state", () => {
  it("points at the step that owns Summarize, not the tab that lacks it", () => {
    summariesState.error = null;
    summariesState.isLoading = false;
    summariesState.data = [];
    const onGotoSummarizeStep = vi.fn();
    render(
      <SummariesView
        documentId="d1"
        categories={[]}
        header={null}
        onGotoSummarizeStep={onGotoSummarizeStep}
      />,
    );
    expect(screen.getByText(/summarization from the Duplicates step/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Go to Duplicates/i }));
    expect(onGotoSummarizeStep).toHaveBeenCalled();
  });
});
