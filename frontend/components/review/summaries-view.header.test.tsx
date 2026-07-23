import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/review-api", () => ({
  getSummaries: vi.fn().mockResolvedValue([]),
  putSummary: vi.fn(),
  resummarize: vi.fn(),
  extractHeader: vi.fn(),
  saveHeader: vi.fn(),
}));

import { extractHeader } from "@/lib/review-api";
import { SummariesView } from "@/components/review/summaries-view";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe("SummariesView header", () => {
  it("renders the same editable report header on the Summaries tab", () => {
    withClient(
      <SummariesView
        documentId="d1"
        categories={[]}
        header={null}
        onHeaderSaved={vi.fn()}
        onGotoReview={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText("First")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Last")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto-fill" })).toBeInTheDocument();
  });

  it("re-detecting on Summaries updates the shared header via onHeaderSaved", async () => {
    const user = userEvent.setup();
    const onHeaderSaved = vi.fn();
    const detected = {
      patient_first_name: "Jane",
      patient_last_name: "Roe",
      patient_dob: "01/02/1990",
      law_firm: "Acme LLP",
    };
    vi.mocked(extractHeader).mockResolvedValue(detected);
    withClient(
      <SummariesView
        documentId="d1"
        categories={[]}
        header={null}
        onHeaderSaved={onHeaderSaved}
        onGotoReview={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Auto-fill" }));
    await waitFor(() => expect(onHeaderSaved).toHaveBeenCalledWith(detected));
  });
});
