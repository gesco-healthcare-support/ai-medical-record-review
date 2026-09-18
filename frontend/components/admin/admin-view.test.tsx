import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { DocumentListItem, DocumentStatus } from "@/lib/types";
import type { AdminCategory } from "@/lib/admin-api";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

// Every hook below hands back the SAME object on every call, deliberately. A factory that builds a
// fresh `vi.fn()` per call lets the component create, update and reprocess with nothing left to
// assert on - the defect class already fixed four times in this phase.
const create = { mutateAsync: vi.fn(), isPending: false };
const update = { mutateAsync: vi.fn(), isPending: false };
const reprocess = { mutateAsync: vi.fn(), isPending: false };

// Mutable, so a test can change what the two queries return BETWEEN renders. That is the only way
// to reach the `find` miss in `runReprocess`; see "stopped being summarized" below.
const docs: { list: DocumentListItem[] } = { list: [] };
const categories: { list: AdminCategory[] } = { list: [] };

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: { is_superuser: true }, isLoading: false }),
}));
vi.mock("@/hooks/use-documents", () => ({ useDocuments: () => ({ data: docs.list }) }));
vi.mock("@/hooks/use-admin", () => ({
  useCategories: () => ({ data: categories.list, isLoading: false }),
  useCreateCategory: () => create,
  useUpdateCategory: () => update,
  useReprocess: () => reprocess,
  useSavePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevertPrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/lib/admin-api", () => ({
  getPrompt: vi
    .fn()
    .mockResolvedValue({ text: "", effective_text: "", builtin_text: "", custom: false }),
}));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { AdminView } from "@/components/admin/admin-view";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return {
    ...result,
    /** Re-render inside the SAME client, so a changed fixture reaches a mounted component. */
    rerenderWith: (next: ReactNode) =>
      result.rerender(<QueryClientProvider client={client}>{next}</QueryClientProvider>),
  };
}

/** One row of the documents list as the admin screen reads it. Synthetic; no patient fields. */
function doc(id: string, original_filename: string, status: DocumentStatus): DocumentListItem {
  return {
    id,
    original_filename,
    status,
    page_count: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    active_job: null,
    rows_count: 0,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
  };
}

function category(over: Partial<AdminCategory> = {}): AdminCategory {
  return {
    id: "3",
    name: "Imaging",
    description: "",
    examples: [],
    auto_assign: true,
    summarize_default: false,
    active: true,
    has_summary_prompt: false,
    ...over,
  };
}

beforeEach(() => {
  // Both fixtures and all three spies are rebuilt per test. The mocks are module-level and shared,
  // so without this a rejection set by one test would still be armed in the next.
  docs.list = [];
  categories.list = [category()];
  create.mutateAsync.mockResolvedValue(undefined);
  update.mutateAsync.mockResolvedValue(undefined);
  reprocess.mutateAsync.mockResolvedValue(undefined);
});

afterEach(() => vi.clearAllMocks());

describe("AdminView error handling", () => {
  it("toasts a humanized message when toggling a category fails", async () => {
    const user = userEvent.setup();
    update.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    withClient(<AdminView />);
    await user.click(await screen.findByRole("button", { name: "Deactivate" }));
    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});

describe("AdminView summarize-default column", () => {
  it("shows a Summarize column reflecting the category flag", async () => {
    withClient(<AdminView />);
    expect(await screen.findByRole("columnheader", { name: "Summarize" })).toBeInTheDocument();
  });

  it("exposes a summarize-by-default toggle in the add-category dialog", async () => {
    const user = userEvent.setup();
    withClient(<AdminView />);
    await user.click(await screen.findByRole("button", { name: /Add category/i }));
    expect(await screen.findByLabelText(/Summarize by default/i)).toBeInTheDocument();
  });
});

describe("AdminView re-run picker", () => {
  it("offers only summarized records for re-running", async () => {
    // The decoy is load-bearing: with a `done`-only fixture, `d.status === "done"` and `true` are
    // indistinguishable, and the filter could be deleted with every assertion still passing.
    docs.list = [
      doc("d-done", "intake-packet.pdf", "done"),
      doc("d-busy", "still-summarizing.pdf", "summarizing"),
    ];
    withClient(<AdminView />);

    const picker = await screen.findByLabelText("Summarized record");
    expect(within(picker).getByRole("option", { name: "intake-packet.pdf" })).toBeInTheDocument();
    expect(within(picker).queryByRole("option", { name: "still-summarizing.pdf" })).toBeNull();
  });

  it("keeps the re-run button disabled until a record is chosen", async () => {
    // This, not the `if (!reprocessId)` early return inside `runReprocess`, is what actually stops
    // an empty re-run. That guard is UNREACHABLE through the interface: the button here is its only
    // caller, and it is disabled whenever the id is empty, so React never fires the click. A probe
    // deleting the guard therefore comes back NO-OP - measured, not assumed, and carried in the
    // probe set with NO-OP as its expected verdict so the claim stays checked.
    docs.list = [doc("d-done", "intake-packet.pdf", "done")];
    const user = userEvent.setup();
    withClient(<AdminView />);

    expect(await screen.findByRole("button", { name: "Re-run summaries" })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Summarized record"), "d-done");
    expect(screen.getByRole("button", { name: "Re-run summaries" })).toBeEnabled();
  });

  it("re-runs the chosen record, names it, and clears the selection", async () => {
    docs.list = [
      doc("d-done", "intake-packet.pdf", "done"),
      doc("d-other", "second-packet.pdf", "done"),
    ];
    const user = userEvent.setup();
    withClient(<AdminView />);

    const picker = await screen.findByLabelText("Summarized record");
    await user.selectOptions(picker, "d-other");
    await user.click(screen.getByRole("button", { name: "Re-run summaries" }));

    expect(reprocess.mutateAsync).toHaveBeenCalledWith("d-other");
    expect(await screen.findByText(/Re-run started for second-packet\.pdf/)).toBeInTheDocument();
    expect(picker).toHaveValue("");
  });

  it("names a record generically once it has stopped being summarized", async () => {
    // `reprocessId` is component state and survives a refetch of the documents list, so a record
    // that leaves `done` while selected leaves the id set and the button live. That is the only
    // route to the `|| "the record"` fallback - the picker's own options are built from the same
    // filtered list, so the lookup cannot miss on the click that follows a selection.
    docs.list = [doc("d-done", "intake-packet.pdf", "done")];
    const user = userEvent.setup();
    const { rerenderWith } = withClient(<AdminView />);
    await user.selectOptions(await screen.findByLabelText("Summarized record"), "d-done");

    docs.list = [doc("d-done", "intake-packet.pdf", "summarizing")];
    rerenderWith(<AdminView />);

    await user.click(screen.getByRole("button", { name: "Re-run summaries" }));

    expect(reprocess.mutateAsync).toHaveBeenCalledWith("d-done");
    expect(await screen.findByText(/Re-run started for the record/)).toBeInTheDocument();
  });

  it("says a re-run failed rather than reporting that it started", async () => {
    docs.list = [doc("d-done", "intake-packet.pdf", "done")];
    reprocess.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    const user = userEvent.setup();
    withClient(<AdminView />);

    await user.selectOptions(await screen.findByLabelText("Summarized record"), "d-done");
    await user.click(screen.getByRole("button", { name: "Re-run summaries" }));

    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
    expect(screen.queryByText(/Re-run started/)).toBeNull();
  });
});

describe("AdminView category editing", () => {
  it("adds a category and reports it", async () => {
    const user = userEvent.setup();
    withClient(<AdminView />);

    await user.click(await screen.findByRole("button", { name: /Add category/i }));
    await user.type(screen.getByLabelText("ID (number)"), "15");
    await user.type(screen.getByLabelText("Name"), "Operative report");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(create.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ id: "15", name: "Operative report" }),
      ),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith("Category added.");
  });

  it("saves an edit to an existing category and reports it", async () => {
    const user = userEvent.setup();
    withClient(<AdminView />);

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Imaging and radiology");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(update.mutateAsync).toHaveBeenCalledWith({
        id: "3",
        body: expect.objectContaining({ name: "Imaging and radiology" }),
      }),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith("Category saved.");
  });

  it("opens the prompt editor for the category whose row was used", async () => {
    // Two categories, and the SECOND row is the one used. With one category, "passes the row's
    // category" and "passes the only category there is" are the same assertion.
    categories.list = [category(), category({ id: "4", name: "Operative report" })];
    const user = userEvent.setup();
    withClient(<AdminView />);

    const rows = await screen.findAllByRole("row");
    await user.click(within(rows[2]).getByRole("button", { name: "Prompt" }));

    expect(
      await screen.findByRole("heading", { name: "Summary prompt - Operative report" }),
    ).toBeInTheDocument();
  });
});
