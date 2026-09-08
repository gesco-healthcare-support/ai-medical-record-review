import { render, screen } from "@testing-library/react";
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
});
