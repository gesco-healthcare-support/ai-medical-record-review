import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/hooks/use-documents", () => ({
  useDocuments: () => ({
    data: [
      {
        id: "b1",
        original_filename: "rec.pdf",
        page_count: 5,
        status: "reviewing",
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
        active_job: null,
        rows_count: 1,
        patient_first_name: "",
        patient_last_name: "",
        patient_name: "",
        patient_dob: "",
        law_firm: "",
      },
    ],
  }),
}));
const downloadBundlePdf = vi.fn();
const downloadBundleSummary = vi.fn();
vi.mock("@/lib/bundle-api", () => ({
  downloadBundlePdf: (...args: unknown[]) => downloadBundlePdf(...args),
  downloadBundleSummary: (...args: unknown[]) => downloadBundleSummary(...args),
}));
vi.mock("@/lib/review-api", () => ({
  getDocument: vi.fn().mockResolvedValue({
    id: "b1",
    original_filename: "rec.pdf",
    page_count: 5,
    status: "reviewing",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    active_job: null,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
    rows: [
      {
        start: 1,
        end: 3,
        category: "3",
        title: "MRI",
        date: "",
        injury_date: "",
        flag: "-",
        suggest_merge: false,
        include: true,
      },
    ],
    categories: [{ id: "3", name: "Imaging" }],
  }),
  extractHeader: vi.fn(),
}));

import { ApiError } from "@/lib/api";
import { getDocument } from "@/lib/review-api";
import { BundlePageClient } from "@/components/bundle/bundle-page-client";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const CONFIG = {
  label: "Diagnostic & Operative",
  slug: "diagnostic-operative",
  categories: ["3", "8"],
};

describe("BundlePageClient error handling", () => {
  it("shows a humanized message when the combined-PDF download fails", async () => {
    const user = userEvent.setup();
    downloadBundlePdf.mockRejectedValue(new ApiError("network", 0));
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(await screen.findByRole("button", { name: /Download combined PDF/i }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });

  it("prefills the export fields from the record's persisted header", async () => {
    const user = userEvent.setup();
    vi.mocked(getDocument).mockResolvedValueOnce({
      id: "b1",
      original_filename: "rec.pdf",
      page_count: 5,
      status: "reviewing",
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      active_job: null,
      patient_first_name: "Jane",
      patient_last_name: "Roe",
      patient_name: "Jane Roe",
      patient_dob: "01/02/1990",
      law_firm: "Acme LLP",
      rows: [
        {
          start: 1,
          end: 3,
          category: "3",
          title: "MRI",
          date: "",
          injury_date: "",
          flag: "-",
          suggest_merge: false,
          include: true,
        },
      ],
      categories: [{ id: "3", name: "Imaging" }],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByLabelText("Patient name")).toHaveValue("Jane Roe");
    expect(screen.getByLabelText("DOB")).toHaveValue("01/02/1990");
    expect(screen.getByLabelText("Attorney law firm")).toHaveValue("Acme LLP");
  });

  it("counts only the documents the record is shipping, matching what the bundle contains", async () => {
    // DEMONSTRATES the bug on this side. The server builds the bundle from included rows only; if
    // this preview counts every row of the category, it promises a document the download does not
    // contain. The reachable case is a resolved duplicate: keep_one unchecks the non-primary copy
    // and leaves its category alone, so it looks identical to a shipping row here.
    const user = userEvent.setup();
    const row = (start: number, include: boolean) => ({
      start,
      end: start,
      category: "3",
      title: "MRI",
      date: "",
      injury_date: "",
      flag: "-",
      suggest_merge: false,
      include,
    });
    vi.mocked(getDocument).mockResolvedValueOnce({
      id: "d1",
      original_filename: "rec.pdf",
      page_count: 3,
      status: "reviewing",
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      active_job: null,
      patient_first_name: "",
      patient_last_name: "",
      patient_name: "",
      patient_dob: "",
      law_firm: "",
      rows: [row(1, true), row(2, false), row(3, true)],
      categories: [{ id: "3", name: "Imaging" }],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByText("2 matching documents")).toBeInTheDocument();
    expect(screen.queryByText("3 matching documents")).not.toBeInTheDocument();
  });
});

/** The failure path above was covered; the success path was not, so nothing pinned that a finished
 *  download tells the reviewer it finished, or that the record id and bundle config reach the API. */
describe("BundlePageClient success path", () => {
  it("confirms a finished combined-PDF download", async () => {
    const user = userEvent.setup();
    downloadBundlePdf.mockResolvedValue(undefined);
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(await screen.findByRole("button", { name: /Download combined PDF/i }));
    expect(await screen.findByText(/Combined PDF downloaded/i)).toBeInTheDocument();
    expect(downloadBundlePdf).toHaveBeenCalledWith("b1", CONFIG);
  });

  it("sends the header fields with the Word report and confirms it", async () => {
    const user = userEvent.setup();
    downloadBundleSummary.mockResolvedValue(undefined);
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(await screen.findByRole("button", { name: /Summarize to Word/i }));
    expect(await screen.findByText(/Word report downloaded/i)).toBeInTheDocument();
    expect(downloadBundleSummary).toHaveBeenCalledWith(
      "b1",
      CONFIG,
      expect.objectContaining({ QMEorAME: expect.any(String) }),
    );
  });
});
