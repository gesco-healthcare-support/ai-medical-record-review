import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExportDialog } from "@/components/review/export-dialog";

describe("ExportDialog error handling", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a friendly fallback when the export request fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("boom")); // network/transport failure
    render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={2}
        excludedCount={0}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(await screen.findByText("Export failed.")).toBeInTheDocument();
  });
});
