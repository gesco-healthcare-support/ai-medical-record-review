import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { Row } from "@/lib/types";

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

  it("does not count a copy the reviewer resolved away as a duplicate", async () => {
    // DEMONSTRATES the bug on this side. The server omits a resolved-away duplicate from the
    // bundle; if this preview counts it, the list promises a document the download does not
    // contain. keep_one marks one member primary and leaves the category alone, so the copy looks
    // identical to a shipping row here.
    //
    // Keyed on the duplicate fields, NOT on `include` - filtering on `include` would have emptied
    // the Depositions preset for older records (see bundles.matched_rows).
    const user = userEvent.setup();
    const row = (start: number, over: Partial<Row> = {}) => ({
      start,
      end: start,
      category: "3",
      title: "MRI",
      date: "",
      injury_date: "",
      flag: "-",
      suggest_merge: false,
      include: true,
      ...over,
    });
    vi.mocked(getDocument).mockResolvedValueOnce({
      id: "d1",
      original_filename: "rec.pdf",
      page_count: 4,
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
        row(1),
        row(2, { dupe_group: 1, dupe_primary: true }),
        row(3, { dupe_group: 1, dupe_primary: false }),
        // Unchecked but NOT a duplicate: still counted, or the Depositions preset breaks.
        row(4, { include: false }),
      ],
      categories: [{ id: "3", name: "Imaging" }],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByText("3 matching documents")).toBeInTheDocument();
    expect(screen.queryByText("4 matching documents")).not.toBeInTheDocument();
  });

  it("counts every member of a cluster nobody has resolved yet", async () => {
    // The state the dedup worker leaves: grouped, NO primary, not dismissed. Reading a row alone
    // treats that as "resolved away" and drops the whole cluster, so the preview would under-count
    // and the download would omit the document entirely. 48 of 138 clusters on the box sit here.
    const user = userEvent.setup();
    const row = (start: number, over: Partial<Row> = {}) => ({
      start,
      end: start,
      category: "3",
      title: "MRI",
      date: "",
      injury_date: "",
      flag: "-",
      suggest_merge: false,
      include: true,
      ...over,
    });
    vi.mocked(getDocument).mockResolvedValueOnce({
      id: "d2",
      original_filename: "rec.pdf",
      page_count: 2,
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
        row(1, { dupe_group: 1, dupe_primary: false }),
        row(2, { dupe_group: 1, dupe_primary: false }),
      ],
      categories: [{ id: "3", name: "Imaging" }],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByText("2 matching documents")).toBeInTheDocument();
  });

  it("says it could not LOAD the record, rather than calling it unidentified", async () => {
    // A failed detail fetch left `detail` undefined, so `rows` was [] and `identified` false - and
    // the screen then asserted "This record hasn't been identified yet" and told the reviewer to go
    // and identify it. Both halves are things the page does not know, and the advice is wrong: the
    // record may be fully identified and the fetch may simply have failed.
    //
    // The sibling screen loading the same document through the same API already reports this
    // honestly ("Could not load this document: ..." in use-review-workflow's boot), so this was the
    // one surface that did not.
    const user = userEvent.setup();
    vi.mocked(getDocument).mockRejectedValueOnce(new ApiError("gone", 404));
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    expect(screen.queryByText(/been identified yet/i)).not.toBeInTheDocument();
  });

  it("still calls a record with no rows unidentified", async () => {
    // GUARDS the new code rather than demonstrating the bug: it passes on origin/main too, where
    // every non-loading state rendered this. It is here because "stop saying unidentified" is the
    // wrong reading of the fix - a record that really has no rows must still say so, and that is
    // the message with the useful next step attached.
    const user = userEvent.setup();
    vi.mocked(getDocument).mockResolvedValueOnce({
      id: "b1",
      original_filename: "rec.pdf",
      page_count: 5,
      status: "uploaded",
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      active_job: null,
      patient_first_name: "",
      patient_last_name: "",
      patient_name: "",
      patient_dob: "",
      law_firm: "",
      rows: [],
      categories: [],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(await screen.findByText(/been identified yet/i)).toBeInTheDocument();
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
