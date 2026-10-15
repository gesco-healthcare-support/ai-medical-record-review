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

const docsState: {
  data: unknown[] | undefined;
  isLoading: boolean;
  isError?: boolean;
  refetch?: () => void;
} = { data: [], isLoading: false };
const upload = { mutateAsync: vi.fn(), isPending: false };
const del = { mutateAsync: vi.fn(), isPending: false };
const identify = { mutateAsync: vi.fn(), isPending: false };
const aggregate = { mutateAsync: vi.fn(), isPending: false };
const assign = { mutateAsync: vi.fn(), isPending: false };
// Who the list was asked for: `useDocuments(owner)` receives the reviewer an admin picked.
const docsArgs: unknown[] = [];
vi.mock("@/hooks/use-documents", () => ({
  useDocuments: (owner?: unknown) => {
    docsArgs.push(owner);
    return docsState;
  },
  useUploadDocument: () => upload,
  useDeleteDocument: () => del,
  useStartIdentification: () => identify,
  useAggregateDocuments: () => aggregate,
  useAssignDocument: () => assign,
}));

// A plain reviewer unless a test says otherwise, so every test written before admins could pick a
// reviewer still sees exactly the page it always did.
const me: { data: { id: number; is_superuser: boolean } | undefined } = {
  data: { id: 1, is_superuser: false },
};
const accountsState: { data: { id: number; name: string; email: string }[] | undefined } = {
  data: undefined,
};
vi.mock("@/hooks/use-current-user", () => ({ useCurrentUser: () => me }));
vi.mock("@/hooks/use-admin", () => ({ useAccounts: () => accountsState }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { DocumentsView } from "@/components/documents/documents-view";

afterEach(() => {
  // `clearAllMocks` resets `mock.calls`, `mock.instances` and `mock.results` and NOTHING ELSE - an
  // implementation set by `mockRejectedValue` survives it and stays armed for every later test in
  // the file. Every rejection below therefore uses `mockRejectedValueOnce`, consumed by the single
  // call it was written for. Resetting `docsState` here is the other half: a mutable module-scope
  // fixture leaks in exactly the same way, just more visibly.
  vi.clearAllMocks();
  docsState.data = [];
  docsState.isLoading = false;
  docsState.isError = false;
  docsState.refetch = undefined;
  docsArgs.length = 0;
  me.data = { id: 1, is_superuser: false };
  accountsState.data = undefined;
  sessionStorage.clear();
});

describe("DocumentsView list failure", () => {
  it("says the list could not be loaded, with a retry, instead of the first-run screen", async () => {
    // A failed fetch leaves `data` undefined, which defaulted to [] and rendered "Start your first
    // review" - telling a reviewer with forty records that they had none.
    const user = userEvent.setup();
    const refetch = vi.fn();
    docsState.data = undefined;
    docsState.isError = true;
    docsState.refetch = refetch;
    render(<DocumentsView />);

    expect(screen.getByRole("alert")).toHaveTextContent(/could not load your documents/i);
    expect(screen.queryByText(/start your first review/i)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
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
    upload.mutateAsync.mockRejectedValueOnce(new ApiError("network", 0));
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
    identify.mutateAsync.mockRejectedValueOnce(new ApiError("a job is already running", 409));
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
    del.mutateAsync.mockRejectedValueOnce(new ApiError("a job is running for this document", 409));
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

    // Into one's own account: straight in, no confirmation, and no owner sent.
    expect(upload.mutateAsync).toHaveBeenCalledWith({ file });
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

describe("DocumentsView for an admin", () => {
  const ACCOUNTS = [
    { id: 1, name: "Adam", email: "adam@example.com" },
    { id: 7, name: "Brian", email: "brian@example.com" },
  ];

  it("shows no reviewer picker to a reviewer who is not an admin", () => {
    // GUARD: the page a reviewer sees is unchanged.
    docsState.data = [doc()];
    render(<DocumentsView />);
    expect(screen.queryByLabelText("Show records for")).toBeNull();
    expect(docsArgs.at(-1)).toBeNull();
  });

  it("offers no Assign to a reviewer who is not an admin", async () => {
    // GUARD: moving records between accounts is an admin's action only.
    const user = userEvent.setup();
    docsState.data = [doc()];
    render(<DocumentsView />);
    await user.click(screen.getAllByRole("button", { name: "Actions" })[0]);
    expect(screen.queryByRole("menuitem", { name: /assign/i })).toBeNull();
  });

  it("starts an admin on their own records", () => {
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    docsState.data = [doc()];
    render(<DocumentsView />);
    expect(screen.getByLabelText("Show records for")).toHaveValue("");
    expect(screen.getByRole("heading", { name: "My documents" })).toBeInTheDocument();
    expect(docsArgs.at(-1)).toBeNull();
  });

  it("lists the other reviewers, not the admin themselves", () => {
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    render(<DocumentsView />);
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual(["My records", "Brian"]);
  });

  it("shows one reviewer's records once the admin picks them", async () => {
    // DEMONSTRATES the request: pick a user, see that user's records only.
    const user = userEvent.setup();
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    docsState.data = [doc()];
    render(<DocumentsView />);
    await user.selectOptions(screen.getByLabelText("Show records for"), "7");
    expect(docsArgs.at(-1)).toBe(7);
    expect(screen.getByRole("heading", { name: "Brian's documents" })).toBeInTheDocument();
    expect(screen.getByText(/recorded under your name/i)).toBeInTheDocument();
  });

  async function viewBrian(user: ReturnType<typeof userEvent.setup>, records = [doc()]) {
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    docsState.data = records;
    const view = render(<DocumentsView />);
    await user.selectOptions(screen.getByLabelText("Show records for"), "7");
    return view;
  }

  const pick = (container: HTMLElement, name = "rec.pdf") => {
    const file = new File([new Uint8Array([1])], name, { type: "application/pdf" });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    return file;
  };

  it("offers both uploads while viewing another reviewer's records", async () => {
    // DEMONSTRATES the request: an admin can upload into the reviewer's page. This used to be
    // pinned the other way, when an upload could only land in the admin's own list.
    const user = userEvent.setup();
    await viewBrian(user);
    expect(screen.getByRole("button", { name: /upload a record/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload split records/i })).toBeInTheDocument();
  });

  it("offers them on an empty list of another reviewer's too", async () => {
    const user = userEvent.setup();
    await viewBrian(user, []);
    expect(screen.getByText("Brian has no records.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload a record/i })).toBeInTheDocument();
  });

  it("asks before uploading into another reviewer's account, and sends it there once confirmed", async () => {
    const user = userEvent.setup();
    upload.mutateAsync.mockResolvedValueOnce({ id: "n1", sha256_duplicate: false });
    const { container } = await viewBrian(user);
    const file = pick(container, "brian.pdf");

    // Nothing is sent until the admin confirms the reviewer the confirmation names.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Upload to Brian's records?");
    expect(dialog).toHaveTextContent('"brian.pdf" will be added');
    expect(upload.mutateAsync).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() =>
      expect(upload.mutateAsync).toHaveBeenCalledWith({ file, owner: 7, allowDuplicate: false }),
    );
    expect(vi.mocked(toast).success).toHaveBeenCalledWith("Record uploaded to Brian's records.");
  });

  it("uploads nothing when the admin cancels", async () => {
    const user = userEvent.setup();
    const { container } = await viewBrian(user);
    pick(container);
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(upload.mutateAsync).not.toHaveBeenCalled();
  });

  it("asks again when the reviewer already has the record, and sends a copy only if confirmed", async () => {
    const user = userEvent.setup();
    upload.mutateAsync
      .mockRejectedValueOnce(
        new ApiError(
          "Brian already has this record (uploaded 10/01/2026, 3 pages).",
          409,
          "duplicate",
        ),
      )
      .mockResolvedValueOnce({ id: "n2", sha256_duplicate: true });
    const { container } = await viewBrian(user);
    const file = pick(container);
    await user.click(await screen.findByRole("button", { name: "Upload" }));

    const dialog = await screen.findByRole("alertdialog", {
      name: "Brian already has this record",
    });
    expect(dialog).toHaveTextContent("(uploaded 10/01/2026, 3 pages). Upload another copy anyway?");
    expect(vi.mocked(toast).error).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Upload anyway" }));
    await waitFor(() =>
      expect(upload.mutateAsync).toHaveBeenLastCalledWith({ file, owner: 7, allowDuplicate: true }),
    );
  });

  it("reports any other refusal instead of offering to upload anyway", async () => {
    const user = userEvent.setup();
    upload.mutateAsync.mockRejectedValueOnce(new ApiError("the reviewer could not be reached", 409));
    const { container } = await viewBrian(user);
    pick(container);
    await user.click(await screen.findByRole("button", { name: "Upload" }));
    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith("the reviewer could not be reached"),
    );
    expect(screen.queryByRole("alertdialog", { name: /already has/ })).toBeNull();
  });

  it("names the reviewer on the split upload too", async () => {
    const user = userEvent.setup();
    await viewBrian(user);
    await user.click(screen.getByRole("button", { name: /upload split records/i }));
    expect(
      screen.getByRole("heading", { name: "Upload split records for Brian" }),
    ).toBeInTheDocument();
  });

  it("offers Assign to an admin, and opens it on the record picked", async () => {
    const user = userEvent.setup();
    await viewBrian(user, [doc({ original_filename: "case.pdf" })]);
    await user.click(await menuItem(user, /^Assign to/));
    expect(screen.getByRole("heading", { name: "Assign this record" })).toBeInTheDocument();
    // Brian owns it, so the assign list offers only the others.
    const assignTo = screen.getByLabelText("Assign to");
    const names = Array.from(assignTo.querySelectorAll("option")).map((o) => o.textContent);
    expect(names).toEqual(["Choose a reviewer", "Adam"]);
  });

  it("remembers the reviewer picked when the admin comes back to the list", () => {
    sessionStorage.setItem("mrr.records.owner", "7");
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    render(<DocumentsView />);
    expect(screen.getByLabelText("Show records for")).toHaveValue("7");
  });

  it("says so when the reviewer picked has no records", async () => {
    const user = userEvent.setup();
    me.data = { id: 1, is_superuser: true };
    accountsState.data = ACCOUNTS;
    docsState.data = [];
    render(<DocumentsView />);
    await user.selectOptions(screen.getByLabelText("Show records for"), "7");
    expect(screen.getByText("Brian has no records.")).toBeInTheDocument();
    expect(screen.queryByText(/start your first review/i)).toBeNull();
  });
});
