/**
 * Re-drafts that outlive the view and run side by side (reviewer report, 2026-11-02).
 *
 * A re-draft is one request held open for the whole model call. The view used to read "which card
 * is re-drafting" from its own mutation, which ended with the view and named only the latest call:
 * after going to My documents and back the card read "Re-draft" while the server still worked, and
 * a second re-draft made the first card look idle. These tests run the REAL hooks against one
 * query client, so a pending re-draft is the mutation cache's, as in the app.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/review-api", () => ({
  getSummaries: vi.fn(),
  putSummary: vi.fn(),
  resummarize: vi.fn(),
  extractHeader: vi.fn(),
  saveHeader: vi.fn(),
}));
// jsdom cannot run the pdf.js iframe.
vi.mock("@/components/review/pdf-viewer", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    PdfViewer: forwardRef(function PdfViewerStub(_props: unknown, ref: unknown) {
      useImperativeHandle(ref as never, () => ({ jumpTo: vi.fn() }), []);
      return <div data-testid="pdf-viewer" />;
    }),
  };
});

import { getSummaries, resummarize } from "@/lib/review-api";
import { MAX_PARALLEL_REDRAFTS } from "@/hooks/use-summaries";
import { SummariesView } from "@/components/review/summaries-view";

const summary = (idx: number) => ({
  idx,
  summaryTitle: `Note ${idx} (Pages ${idx + 1}-${idx + 1})`,
  summaryDate: "01/02/2026",
  summaryText: `Body ${idx}.`,
  manualCheck: false,
  excluded: false,
  edited: false,
  verified: false,
  verifyChanged: false,
  verifyIssues: [],
  row: { start: idx + 1, end: idx + 1, category: "1" },
});

/** Each re-draft waits until the test finishes it, so it stays pending as long as needed. */
function heldRedrafts() {
  const finish = new Map<number, () => void>();
  vi.mocked(resummarize).mockImplementation(
    (_documentId: string, idx: number) =>
      new Promise((resolve) => finish.set(idx, () => resolve(summary(idx) as never))) as never,
  );
  return finish;
}

function view() {
  return (
    <SummariesView documentId="d1" categories={[]} header={null} onGotoSummarizeStep={vi.fn()} />
  );
}

async function mounted(client: QueryClient) {
  const rendered = render(<QueryClientProvider client={client}>{view()}</QueryClientProvider>);
  await screen.findByText("Body 0.");
  return rendered;
}

/** The card whose body text is ``Body {idx}.``, scoped so its buttons are its own. */
function card(idx: number) {
  return within(screen.getByText(`Body ${idx}.`).closest(".summary-card") as HTMLElement);
}

function redraftButton(idx: number) {
  return card(idx).getByRole("button", { name: /^Re-draft/ });
}

afterEach(() => vi.clearAllMocks());

describe("SummariesView re-drafts", () => {
  it("shows every running re-draft as running, not only the latest", async () => {
    vi.mocked(getSummaries).mockResolvedValue([0, 1, 2, 3].map(summary) as never);
    const finish = heldRedrafts();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await mounted(client);

    act(() => redraftButton(0).click());
    act(() => redraftButton(2).click());

    await waitFor(() => expect(redraftButton(2)).toHaveTextContent("Re-drafting..."));
    expect(redraftButton(0)).toHaveTextContent("Re-drafting...");
    expect(redraftButton(0)).toBeDisabled();
    expect(redraftButton(1)).toHaveTextContent(/^Re-draft$/);
    expect(resummarize).toHaveBeenCalledTimes(2);

    await act(async () => finish.get(0)?.());
    await waitFor(() => expect(redraftButton(0)).toHaveTextContent(/^Re-draft$/));
    expect(redraftButton(2)).toHaveTextContent("Re-drafting...");
  });

  it("still shows a re-draft as running after leaving the view and coming back", async () => {
    vi.mocked(getSummaries).mockResolvedValue([0, 1].map(summary) as never);
    const finish = heldRedrafts();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const first = await mounted(client);
    act(() => redraftButton(1).click());
    await waitFor(() => expect(redraftButton(1)).toHaveTextContent("Re-drafting..."));

    first.unmount(); // the reviewer goes to My documents; the request is still open
    await mounted(client);

    expect(redraftButton(1)).toHaveTextContent("Re-drafting...");
    expect(redraftButton(1)).toBeDisabled();
    await act(async () => finish.get(1)?.());
    await waitFor(() => expect(redraftButton(1)).toHaveTextContent(/^Re-draft$/));
  });

  it("holds the other cards' Re-draft while the most allowed are running", async () => {
    const count = MAX_PARALLEL_REDRAFTS + 1;
    vi.mocked(getSummaries).mockResolvedValue(
      Array.from({ length: count }, (_, idx) => summary(idx)) as never,
    );
    const finish = heldRedrafts();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await mounted(client);

    for (let idx = 0; idx < MAX_PARALLEL_REDRAFTS; idx++) {
      act(() => redraftButton(idx).click());
    }

    const last = MAX_PARALLEL_REDRAFTS;
    await waitFor(() => expect(redraftButton(last)).toBeDisabled());
    expect(redraftButton(last)).toHaveTextContent(/^Re-draft$/);
    expect(redraftButton(last)).toHaveAttribute("title", expect.stringMatching(/wait for one/));

    await act(async () => finish.get(0)?.());
    await waitFor(() => expect(redraftButton(last)).toBeEnabled());
  });
});
