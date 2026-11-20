import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const sheetQuery: { data?: unknown; isError: boolean; error: unknown } = {
  data: undefined,
  isError: false,
  error: null,
};
const save = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({
  useBillingSheet: () => sheetQuery,
  useSaveBillingSheet: () => save,
}));

const download = vi.fn();
vi.mock("@/lib/documents-api", () => ({
  downloadBillingSheet: (id: string) => download(id),
}));

import { toast } from "sonner";
import { BillingSheetDialog } from "@/components/documents/billing-sheet-dialog";
import { ApiError } from "@/lib/api";
import type { BillingSheet } from "@/lib/billing-sheet";
import type { DocumentListItem } from "@/lib/types";

const DOC = { id: "d1", received_on: "2026-11-19" } as DocumentListItem;

const SHEET: BillingSheet = {
  visit_type: "QME",
  appointment_date: "2026-12-01",
  doctor: "Alpha",
  review_source: "in_house",
  interpreter: "yes",
  claim_numbers: "ADJ1",
  previous_visit: "",
  declared_pages: 42,
  discrepancy_reason: "",
  records_from: "defense",
  received_on: "2026-11-19",
  office_count: "",
  depo_pages: "",
  counted: { office: 42, depo: 7 },
  pages_counted: 42,
  pages_unread: 0,
  doctors: ["Alpha", "Beta"],
};

/** Open the dialog on the sheet `SHEET` changed by `sheet`, or on no sheet yet (`null`). */
function show(sheet: Partial<BillingSheet> | null = {}, onClose = vi.fn()) {
  sheetQuery.data = sheet === null ? undefined : { ...SHEET, ...sheet };
  const view = render(<BillingSheetDialog doc={DOC} onClose={onClose} />);
  const rerender = () => view.rerender(<BillingSheetDialog doc={DOC} onClose={onClose} />);
  return { onClose, rerender };
}

const button = (name: string) => screen.getByRole("button", { name });

afterEach(() => {
  vi.clearAllMocks();
  sheetQuery.data = undefined;
  sheetQuery.isError = false;
  sheetQuery.error = null;
  save.isPending = false;
});

describe("BillingSheetDialog", () => {
  it("shows what was typed at upload and the counts worked out from the record", () => {
    show();

    expect(screen.getByLabelText("Type of visit")).toHaveValue("QME");
    expect(screen.getByLabelText("Appointment date")).toHaveValue("2026-12-01");
    expect(screen.getByLabelText("Doctor")).toHaveValue("Alpha");
    expect(screen.getByLabelText("Reviewed by")).toHaveValue("in_house");
    expect(screen.getByLabelText("Interpreter")).toHaveValue("yes");
    expect(screen.getByLabelText("Records from")).toHaveValue("defense");
    expect(screen.getByLabelText("Office count")).toHaveAttribute("placeholder", "42");
    expect(screen.getByLabelText("Deposition pages")).toHaveAttribute("placeholder", "7");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Worked out from the record: office count 42, deposition pages 7.",
    );
    expect(button("Save")).toBeDisabled();
    expect(button("Download PDF")).toBeEnabled();
  });

  it("says the pages are still being read, and holds the download until they are", () => {
    show({ counted: null, pages_unread: 10 });

    expect(screen.getByRole("status")).toHaveTextContent(
      "Reading the record's pages: 32 of 42 read.",
    );
    expect(screen.getByLabelText("Office count")).toHaveAttribute(
      "placeholder",
      "Worked out once read",
    );
    expect(button("Download PDF")).toBeDisabled();
    expect(button("Download PDF")).toHaveAttribute("title", "Ready once every page is read.");
  });

  it("downloads once the counts are typed, even while the pages are read", () => {
    show({ counted: null, pages_unread: 10, office_count: 40, depo_pages: 0 });

    expect(button("Download PDF")).toBeEnabled();
  });

  it("downloads the sheet's PDF", async () => {
    const user = userEvent.setup();
    show();

    await user.click(button("Download PDF"));

    expect(download).toHaveBeenCalledWith("d1");
  });

  it("saves an edit, and holds the download until it is saved", async () => {
    const user = userEvent.setup();
    save.mutateAsync.mockResolvedValue({ ...SHEET, discrepancy_reason: "Two pages missing" });
    show();

    fireEvent.change(screen.getByLabelText("Reason for discrepancy"), {
      target: { value: "Two pages missing" },
    });

    expect(button("Download PDF")).toBeDisabled();
    expect(button("Download PDF")).toHaveAttribute("title", "Save the changes first.");
    await user.click(button("Save"));

    expect(save.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ discrepancy_reason: "Two pages missing", visit_type: "QME" }),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith("Billing sheet saved.");
  });

  it("takes the server's answer as the saved state", async () => {
    // The server drops the appointment of a supplemental report: the form follows its answer.
    const user = userEvent.setup();
    const saved = { ...SHEET, visit_type: "SUPP", appointment_date: "" };
    save.mutateAsync.mockImplementation(async () => {
      sheetQuery.data = saved; // as the hook puts the answer in the cache
      return saved;
    });
    const { rerender } = show();

    await user.selectOptions(screen.getByLabelText("Type of visit"), "SUPP");
    expect(screen.queryByLabelText("Appointment date")).toBeNull();
    await user.click(button("Save"));
    await waitFor(() => expect(vi.mocked(toast).success).toHaveBeenCalled());
    rerender();

    expect(button("Save")).toBeDisabled();
    expect(button("Download PDF")).toBeEnabled();
  });

  it("says when the save is refused", async () => {
    const user = userEvent.setup();
    save.mutateAsync.mockRejectedValue(new ApiError("Choose the doctor from the list.", 400));
    show();

    await user.selectOptions(screen.getByLabelText("Doctor"), "Beta");
    await user.click(button("Save"));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith("Choose the doctor from the list."),
    );
  });

  it("points at a declared count the office count does not match", () => {
    show({ declared_pages: 40 });

    expect(
      screen.getByText(/The declaration states 40 pages; the office count is 42\./),
    ).toBeInTheDocument();
  });

  it("asks for whole-number counts before it saves", () => {
    show();

    fireEvent.change(screen.getByLabelText("Office count"), { target: { value: "many" } });

    expect(button("Save")).toBeDisabled();
    expect(screen.getByText(/Counts are whole numbers/)).toBeInTheDocument();
  });

  it("keeps a doctor that is no longer on the list", () => {
    show({ doctor: "Retired" });

    expect(screen.getByLabelText("Doctor")).toHaveValue("Retired");
  });

  it("closes", async () => {
    const user = userEvent.setup();
    const { onClose } = show();

    await user.click(button("Done"));

    expect(onClose).toHaveBeenCalled();
  });

  it("waits for the sheet", () => {
    show(null);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("says when the record is gone", () => {
    sheetQuery.isError = true;
    sheetQuery.error = new ApiError("not found", 404);
    show(null);

    expect(screen.getByText("This record no longer exists. Refresh the list.")).toBeInTheDocument();
  });

  it("is closed with no record", () => {
    render(<BillingSheetDialog doc={null} onClose={vi.fn()} />);

    expect(screen.queryByText("Billing sheet")).toBeNull();
  });
});
