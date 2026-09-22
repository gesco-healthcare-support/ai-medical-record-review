import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { toast } from "sonner";

import { CategoryDialog } from "@/components/admin/category-dialog";
import { ApiError } from "@/lib/api";

function renderDialog(onCreate = vi.fn()) {
  render(
    <CategoryDialog
      open
      onOpenChange={vi.fn()}
      editing={null}
      onCreate={onCreate}
      onUpdate={vi.fn()}
      saving={false}
    />,
  );
}

describe("CategoryDialog error handling", () => {
  it("humanizes a network failure on save", async () => {
    const user = userEvent.setup();
    renderDialog(vi.fn().mockRejectedValue(new ApiError("network", 0)));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });

  it("passes through an actionable server message (409 duplicate id)", async () => {
    const user = userEvent.setup();
    renderDialog(vi.fn().mockRejectedValue(new ApiError("category 15 already exists", 409)));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("category 15 already exists")).toBeInTheDocument();
  });

  it("includes summarize_default in the payload and reflects the toggle", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog(onCreate);
    await user.click(screen.getByLabelText(/Summarize by default/i)); // default on -> toggle off
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ summarize_default: false }),
    );
  });

  it("opens wide so description and examples are usable", () => {
    renderDialog();
    // .ev-dialog-wide (evaluators-ds.css) replaces the 384px shadcn default.
    expect(screen.getByRole("dialog")).toHaveClass("ev-dialog-wide");
  });
});

describe("CategoryDialog dismissal", () => {
  beforeEach(() => {
    vi.mocked(toast.error).mockClear();
  });

  // #264 REVERSES what this block used to pin. The previous version asserted the dialog refused
  // to close while `saving`, which left Escape, an overlay click and the corner button silently
  // doing nothing while the close button still rendered as active - so a hung save trapped the
  // reviewer with no explanation. The defect the guard was reaching for is fixed at its source
  // instead: the failure is toasted, and `Toaster` lives in the root layout, so it survives the
  // dialog closing.
  it("closes on Escape even while a save is in flight", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <CategoryDialog
        open
        onOpenChange={onOpenChange}
        editing={null}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        saving
      />,
    );
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("toasts a failed save, so a dismissal cannot swallow it", async () => {
    // The half that makes allowing the dismissal safe. Without this the message existed only in
    // `error` state rendered inside the dialog, so closing mid-save made a failure look like a
    // success. Fails on origin/main.
    const user = userEvent.setup();
    render(
      <CategoryDialog
        open
        onOpenChange={vi.fn()}
        editing={null}
        onCreate={vi.fn().mockRejectedValue(new ApiError("network", 0))}
        onUpdate={vi.fn()}
        saving={false}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/couldn.t reach the server/i));
  });

  it("closes without saving when Cancel is pressed", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const onCreate = vi.fn();
    render(
      <CategoryDialog
        open
        onOpenChange={onOpenChange}
        editing={null}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        saving={false}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    // The positive comes first on purpose. "onCreate was not called" is true of a dialog whose
    // Cancel button does nothing at all, so on its own it would pass against a dead control.
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onCreate).not.toHaveBeenCalled();
  });
});

describe("CategoryDialog payload", () => {
  it("sends every field the reviewer filled in, with the examples list split and cleaned", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog(onCreate);

    // Padding on the text fields and a blank line plus an indented line in the examples box are
    // all load-bearing. Without them `split("\n")` and `split("\n").map(trim).filter(Boolean)`
    // produce the same array, and the cleanup could be deleted with this test still green.
    //
    // `fireEvent.change` rather than `user.type`, and the CONTENT above is why that is safe:
    // what this test pins is the payload, not per-keystroke behaviour, and every field here is a
    // plain controlled input (`onChange={(e) => setX(e.target.value)}`) with no validation gating
    // Save. The same strings reach the same state either way.
    //
    // It was `user.type`, which types character by character with a delay between keystrokes, and
    // that cost about 3.2s against a 5000ms default - close enough that the test timed out twice
    // under full-suite load and passed 8/8 in isolation. Four fields, one of them a 37-character
    // string with two newlines, is the whole of the difference.
    //
    // Proven rather than argued: the four mutation probes on the cleanup were run against BOTH the
    // typed version and this one and returned identical verdicts, so the change is faster without
    // pinning less. Reserve `user.type` for where per-keystroke behaviour is actually under test.
    fireEvent.change(screen.getByLabelText("ID (number)"), { target: { value: " 15 " } });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "  Operative report  " },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "  Surgery notes  " },
    });
    fireEvent.change(screen.getByLabelText(/Example document titles/i), {
      target: { value: "MRI Report\n\n   CT Scan   " },
    });
    await user.click(screen.getByLabelText(/Auto-assign/i)); // default on -> off
    await user.click(screen.getByLabelText("Active")); // default on -> off
    await user.click(screen.getByRole("button", { name: "Save" }));

    // Exact, not objectContaining - but NOT because objectContaining would miss a dropped field.
    // It would not: it requires the received object to carry every property listed, so with all
    // seven enumerated an omission fails under either matcher. Measured, not reasoned:
    //
    //   omission    objectContaining FAILS   exact FAILS
    //   extra key   objectContaining PASSES  exact FAILS   <- the only real difference
    //
    // The EXTRA field is what exact buys, and it is the one worth buying for a body going to a
    // server: a form field leaking into the payload, or a rename arriving alongside the name it
    // replaced. It also pins the payload's SHAPE as a contract, so a field added to the dialog but
    // not to the API type fails here rather than being sent silently.
    expect(onCreate).toHaveBeenCalledWith({
      id: "15",
      name: "Operative report",
      description: "Surgery notes",
      examples: ["MRI Report", "CT Scan"],
      auto_assign: false,
      summarize_default: true,
      active: false,
    });
  });
});
