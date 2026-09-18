import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

// HOISTED, all of them. Each of these used to be a fresh `vi.fn()` created inside the factory, so
// the thing it stood for could be exercised but never asserted on - the router push, the delete,
// the identify. That is the same defect class as three stubs already fixed in this phase: a
// substituted dependency too inert for the component's own wiring to be observed.
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const docsState: { data: unknown[]; isLoading: boolean } = { data: [], isLoading: false };
const upload = { mutateAsync: vi.fn(), isPending: false };
const del = { mutateAsync: vi.fn(), isPending: false };
const identify = { mutateAsync: vi.fn(), isPending: false };
const aggregate = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({
  useDocuments: () => docsState,
  useUploadDocument: () => upload,
  useDeleteDocument: () => del,
  useStartIdentification: () => identify,
  useAggregateDocuments: () => aggregate,
}));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { DocumentsView } from "@/components/documents/documents-view";

afterEach(() => {
  vi.clearAllMocks();
  docsState.data = [];
  docsState.isLoading = false;
});

const doc = (over: Record<string, unknown> = {}) => ({
  id: "d1",
  original_filename: "record.pdf",
  patient_name: "",
  page_count: 4,
  rows_count: 0,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  status: "uploaded",
  active_job: null,
  ...over,
});

/** Open a record's actions menu and pick an item by name. The menu is a Radix dropdown, so the
 *  content is portalled and only exists once the trigger has been activated. */
async function menuItem(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getAllByRole("button", { name: "Actions" })[0]);
  return screen.getByRole("menuitem", { name });
}

describe("DocumentsView error handling", () => {
  it("toasts a humanized message when an upload fails", async () => {
    upload.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    const { container } = render(<DocumentsView />);
    const file = new File([new Uint8Array([1, 2, 3])], "rec.pdf", { type: "application/pdf" });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});

describe("DocumentsView upload affordances", () => {
  it("binds the drop area to the file input and opens the picker once from each control", () => {
    const { container, getByText } = render(<DocumentsView />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const label = container.querySelector("label.hd-drop") as HTMLLabelElement;

    // The binding itself: the drop area is a real <label> for the input, which is what lets the
    // browser open the picker without a click handler on a non-interactive element.
    expect(label).not.toBeNull();
    expect(label.htmlFor).toBe(input.id);
    expect(input.id).not.toBe("");

    const clicks = vi.fn();
    input.addEventListener("click", clicks);

    fireEvent.click(label);
    expect(clicks).toHaveBeenCalledTimes(1);

    // "Browse files" is interactive content inside the label, so per the HTML spec clicking it does
    // NOT also activate the label. It must open the picker exactly once more, never twice.
    fireEvent.click(getByText("Browse files"));
    expect(clicks).toHaveBeenCalledTimes(2);
  });
});

describe("DocumentsView re-identification guard", () => {
  it("asks before re-identifying a record that already has documents, and does not start until confirmed", async () => {
    // Re-running identification REPLACES the document list and every boundary and category the
    // reviewer corrected by hand. Asserting only that the dialog appears would pass on a build that
    // opens the dialog AND starts the run anyway, so the not-called assertion is the real one.
    const user = userEvent.setup();
    docsState.data = [doc({ rows_count: 7 })];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /Re-run identification/));

    expect(identify.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /Re-run identification\?/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Re-run$/ }));
    await waitFor(() => expect(identify.mutateAsync).toHaveBeenCalledWith("d1"));
  });

  it("starts identification immediately on a record with nothing found yet", async () => {
    // The decoy for the one conjunct in `if (doc.rows_count)`. Nothing has been corrected on this
    // record, so a confirmation would be a prompt with no decision behind it.
    const user = userEvent.setup();
    docsState.data = [doc({ rows_count: 0 })];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /Start identification/));

    await waitFor(() => expect(identify.mutateAsync).toHaveBeenCalledWith("d1"));
    expect(screen.queryByRole("heading", { name: /Re-run identification\?/ })).toBeNull();
  });

  it("says so when identification cannot be started", async () => {
    const user = userEvent.setup();
    identify.mutateAsync.mockRejectedValue(new ApiError("a job is already running", 409));
    docsState.data = [doc({ rows_count: 0 })];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /Start identification/));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringContaining("a job is already running"),
      ),
    );
  });
});

describe("DocumentsView delete", () => {
  it("asks before deleting, and deletes only once confirmed", async () => {
    const user = userEvent.setup();
    docsState.data = [doc({ rows_count: 3 })];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /Delete/));

    expect(del.mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /Delete this record\?/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Delete$/ }));
    await waitFor(() => expect(del.mutateAsync).toHaveBeenCalledWith("d1"));
  });

  it("says so when a delete is refused rather than reporting it gone", async () => {
    // A record that still exists but reads as deleted is worse than an error: the reviewer moves on.
    const user = userEvent.setup();
    del.mutateAsync.mockRejectedValue(new ApiError("a job is running for this document", 409));
    docsState.data = [doc({})];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /Delete/));
    await user.click(screen.getByRole("button", { name: /^Delete$/ }));

    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringContaining("a job is running for this document"),
      ),
    );
  });
});

describe("DocumentsView drop target", () => {
  it("uploads a dropped file, and stops the browser opening it instead", () => {
    // Without preventDefault on BOTH dragover and drop, the browser navigates to the dropped PDF and
    // the reviewer loses whatever they had open. fireEvent returns false when the event was
    // cancelled, which is the only direct evidence preventDefault ran.
    const { container } = render(<DocumentsView />);
    const main = container.querySelector("main") as HTMLElement;
    const file = new File([new Uint8Array([1])], "dropped.pdf", { type: "application/pdf" });

    expect(fireEvent.dragOver(main)).toBe(false);
    fireEvent.dragLeave(main);
    expect(fireEvent.drop(main, { dataTransfer: { files: [file] } })).toBe(false);

    expect(upload.mutateAsync).toHaveBeenCalledWith(file);
  });
});

describe("DocumentsView navigation", () => {
  it("opens the record that was clicked", async () => {
    const user = userEvent.setup();
    docsState.data = [doc({ id: "abc123" })];
    render(<DocumentsView />);

    await user.click(await menuItem(user, /^Open$/));

    // The id in the path is the whole point: routing to the wrong record shows another patient's
    // documents to a reviewer who thinks they opened their own.
    expect(push).toHaveBeenCalledWith("/records/abc123");
  });

  it("opens the split-upload dialog from its own button", async () => {
    const user = userEvent.setup();
    docsState.data = [doc({})];
    render(<DocumentsView />);

    await user.click(screen.getByRole("button", { name: /Upload split records/ }));

    expect(screen.getByRole("heading", { name: /Upload split records/ })).toBeInTheDocument();
  });
});
