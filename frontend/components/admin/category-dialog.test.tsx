import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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
});
