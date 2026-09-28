import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { Row } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// The watch's own timing is covered by hooks/use-download-watch.test.tsx. Here a handed-over download reads
// as finished unless a test says otherwise, so these tests check only what the page does with the watch.
vi.mock("@/hooks/use-download-watch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-download-watch")>();
  return {
    ...actual,
    useDownloadWatch: vi.fn((prepared: unknown) =>
      prepared
        ? { message: actual.COMPLETE, tone: "ok", watching: false }
        : { message: "", tone: "info", watching: false },
    ),
  };
});
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
import { COMPLETE, DOWNLOADING, useDownloadWatch } from "@/hooks/use-download-watch";

function withClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

/** What a bundle POST answers with since #389; synthetic. */
const PREPARED = { token: "tok", url: "/api/documents/b1/downloads/tok", filename: "b.pdf", size: 4 };

const CONFIG = {
  label: "Diagnostic & Operative",
  slug: "diagnostic-operative",
  categories: ["3", "8"],
  downloadName: "List of Diagnostic and Operative Reports",
};

describe("BundlePageClient error handling", () => {
  it("shows a humanized message when the combined-PDF download fails", async () => {
    const user = userEvent.setup();
    downloadBundlePdf.mockRejectedValue(new ApiError("network", 0));
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(
      await screen.findByRole("button", { name: /Download combined PDF/i }),
    );
    expect(
      await screen.findByText(/couldn't reach the server/i),
    ).toBeInTheDocument();
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

    expect(await screen.findByLabelText("Patient name")).toHaveValue(
      "Jane Roe",
    );
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

  it("accepts an edit to every export header field", async () => {
    // The four fields render from props and write back through onChange. Asserting only that they
    // render leaves the write-back path - the half that actually carries the reviewer's typing into
    // the export - unexercised.
    const user = userEvent.setup();
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    // Short, DISTINCT values: each field only has to show that its own typing came back through
    // onChange. Every keystroke re-renders the whole page, and 51 characters of typing is what took
    // this test past the 5000ms timeout under load.
    const patient = await screen.findByLabelText("Patient name");
    await user.type(patient, "Roe");
    expect(patient).toHaveValue("Roe");

    const dob = screen.getByLabelText("DOB");
    await user.type(dob, "01/02/1990");
    expect(dob).toHaveValue("01/02/1990");

    const qme = screen.getByLabelText("Evaluation type (QME / AME)");
    await user.clear(qme);
    await user.type(qme, "AME");
    expect(qme).toHaveValue("AME");

    const firm = screen.getByLabelText("Attorney law firm");
    await user.type(firm, "Acme");
    expect(firm).toHaveValue("Acme");
  });

  // A per-keystroke trim would turn "a b" into "ab" - see the same tests in header-bar.test.tsx.
  // Evaluation type is the one field that ships prefilled, so it alone is cleared first.
  it.each<[string, boolean]>([
    ["Patient name", false],
    ["Evaluation type (QME / AME)", true],
    ["Attorney law firm", false],
  ])("keeps a space typed into the export field %s", async (label, startsPrefilled) => {
    const user = userEvent.setup();
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    const box = await screen.findByLabelText(label);
    if (startsPrefilled) await user.clear(box);
    await user.type(box, "a b");
    expect(box).toHaveValue("a b");
  });

  it("shows the empty state rather than the table when nothing matches the preset", async () => {
    // The empty-vs-table branch was INERT: rendering the table unconditionally left every other test
    // in this file green. Pinned here because the matches card is about to move into its own
    // component, and "no documents here" is the whole point of that card on an unmatched record.
    const user = userEvent.setup();
    vi.mocked(getDocument).mockResolvedValueOnce({
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
          category: "1", // deliberately OUTSIDE CONFIG.categories, which is ["3", "8"]
          title: "Progress note",
          date: "",
          injury_date: "",
          flag: "-",
          suggest_merge: false,
          include: true,
        },
      ],
      categories: [{ id: "1", name: "Progress report" }],
    });
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));

    expect(
      await screen.findByText(`No ${CONFIG.label} documents here`),
    ).toBeInTheDocument();
    expect(screen.getByText("0 matching documents")).toBeInTheDocument();
    // The row exists on the record but must not be listed: it is not part of this preset.
    expect(screen.queryByText("Progress note")).not.toBeInTheDocument();
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
  it("keeps both downloads off while a handed-over download is being watched (#390)", async () => {
    const user = userEvent.setup();
    const watch = vi.mocked(useDownloadWatch);
    const original = watch.getMockImplementation();
    watch.mockImplementation((prepared) =>
      prepared
        ? { message: DOWNLOADING, tone: "info", watching: true }
        : { message: "", tone: "info", watching: false },
    );
    try {
      downloadBundlePdf.mockResolvedValue(PREPARED);
      withClient(<BundlePageClient config={CONFIG} />);
      await user.click(await screen.findByRole("button", { name: "Select" }));
      await user.click(await screen.findByRole("button", { name: /Download combined PDF/i }));

      expect(await screen.findByText(DOWNLOADING)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download combined PDF/i })).toBeDisabled();
      expect(screen.getByRole("button", { name: /Summarize to Word/i })).toBeDisabled();
    } finally {
      if (original) watch.mockImplementation(original);
    }
  });

  // CHANGED EXPECTATION (#390), deliberately: these said "Combined PDF downloaded." / "Word report
  // downloaded." the moment the link was handed to the browser - before anything had arrived. The line now
  // reports what the server saw happen to the download, so "Download complete." means it finished.
  it("confirms a finished combined-PDF download", async () => {
    const user = userEvent.setup();
    downloadBundlePdf.mockResolvedValue(PREPARED);
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(
      await screen.findByRole("button", { name: /Download combined PDF/i }),
    );
    expect(await screen.findByText(COMPLETE)).toBeInTheDocument();
    expect(downloadBundlePdf).toHaveBeenCalledWith("b1", CONFIG);
    expect(vi.mocked(useDownloadWatch)).toHaveBeenLastCalledWith(PREPARED);
  });

  it("sends the header fields with the Word report and confirms it", async () => {
    const user = userEvent.setup();
    downloadBundleSummary.mockResolvedValue(PREPARED);
    withClient(<BundlePageClient config={CONFIG} />);
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(
      await screen.findByRole("button", { name: /Summarize to Word/i }),
    );
    expect(await screen.findByText(COMPLETE)).toBeInTheDocument();
    expect(downloadBundleSummary).toHaveBeenCalledWith(
      "b1",
      CONFIG,
      expect.objectContaining({ QMEorAME: expect.any(String) }),
    );
  });
});
