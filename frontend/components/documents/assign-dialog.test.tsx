import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const assign = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({ useAssignDocument: () => assign }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { AssignDialog } from "@/components/documents/assign-dialog";
import type { DocumentListItem } from "@/lib/types";

const ACCOUNTS = [
  { id: 1, name: "Adam", email: "adam@example.com" },
  { id: 7, name: "Brian", email: "brian@example.com" },
  { id: 9, name: "", email: "bernie@example.com" },
];

const doc = {
  id: "d1",
  original_filename: "case.pdf",
  patient_name: "",
  page_count: 4,
  rows_count: 0,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  status: "uploaded",
  active_job: null,
} as unknown as DocumentListItem;

afterEach(() => vi.clearAllMocks());

function show(onClose = vi.fn()) {
  render(<AssignDialog doc={doc} accounts={ACCOUNTS} currentOwnerId={7} onClose={onClose} />);
  return onClose;
}

describe("AssignDialog", () => {
  it("offers every reviewer but the record's owner, by name or email", () => {
    show();
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["Choose a reviewer", "Adam", "bernie@example.com"]);
  });

  it("cannot assign until a reviewer is chosen", () => {
    show();
    expect(screen.getByRole("button", { name: "Assign" })).toBeDisabled();
  });

  it("moves the record to the reviewer chosen, says so, and closes", async () => {
    const user = userEvent.setup();
    assign.mutateAsync.mockResolvedValueOnce({ id: "d1", owner: { id: 9, name: "" } });
    const onClose = show();
    await user.selectOptions(screen.getByLabelText("Assign to"), "9");
    await user.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(() =>
      expect(assign.mutateAsync).toHaveBeenCalledWith({
        id: "d1",
        owner: 9,
        allowDuplicate: false,
      }),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith(
      "Record assigned to bernie@example.com.",
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows why when the reviewer already has the file, and assigns only on a second yes", async () => {
    const user = userEvent.setup();
    assign.mutateAsync
      .mockRejectedValueOnce(
        new ApiError("Adam already has this record (uploaded 10/01/2026, 4 pages).", 409, "duplicate"),
      )
      .mockResolvedValueOnce({ id: "d1", owner: { id: 1, name: "Adam" } });
    const onClose = show();
    await user.selectOptions(screen.getByLabelText("Assign to"), "1");
    await user.click(screen.getByRole("button", { name: "Assign" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Adam already has this record (uploaded 10/01/2026, 4 pages). Assign it anyway?",
    );
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Assign anyway" }));
    await waitFor(() =>
      expect(assign.mutateAsync).toHaveBeenLastCalledWith({
        id: "d1",
        owner: 1,
        allowDuplicate: true,
      }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("forgets the duplicate warning when a different reviewer is chosen", async () => {
    // The warning was about the previous choice; carrying it over would assign anyway to someone
    // nobody was warned about.
    const user = userEvent.setup();
    assign.mutateAsync.mockRejectedValueOnce(new ApiError("Adam already has it.", 409, "duplicate"));
    show();
    await user.selectOptions(screen.getByLabelText("Assign to"), "1");
    await user.click(screen.getByRole("button", { name: "Assign" }));
    await screen.findByRole("alert");

    await user.selectOptions(screen.getByLabelText("Assign to"), "9");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Assign" })).toBeEnabled();
  });

  it("reports any other refusal and stays open", async () => {
    const user = userEvent.setup();
    assign.mutateAsync.mockRejectedValueOnce(
      new ApiError("a job is running for this document; wait for it before assigning it", 409),
    );
    const onClose = show();
    await user.selectOptions(screen.getByLabelText("Assign to"), "1");
    await user.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        "a job is running for this document; wait for it before assigning it",
      ),
    );
    expect(screen.queryByRole("button", { name: "Assign anyway" })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("is closed while no record is picked", () => {
    render(<AssignDialog doc={null} accounts={ACCOUNTS} currentOwnerId={7} onClose={vi.fn()} />);
    expect(screen.queryByRole("heading", { name: "Assign this record" })).toBeNull();
  });
});
