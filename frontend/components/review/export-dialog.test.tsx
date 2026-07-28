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

describe("ExportDialog page numbers", () => {
  afterEach(() => vi.restoreAllMocks());

  function mockFetch() {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      blob: async () => new Blob(["x"]),
    } as unknown as Response);
    // jsdom implements neither of these; the download path calls both.
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    return fetchSpy;
  }

  function body(fetchSpy: ReturnType<typeof mockFetch>) {
    return JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
  }

  function open() {
    render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={1}
        excludedCount={0}
      />,
    );
  }

  it("is unchecked by default, so the export asks for no page numbers", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    expect(screen.getByLabelText(/include page numbers/i)).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(body(fetchSpy).includePageNumbers).toBe(false);
  });

  it("resets to unchecked when the dialog is reopened", async () => {
    const user = userEvent.setup();
    mockFetch();
    const { rerender } = render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={1}
        excludedCount={0}
      />,
    );
    await user.click(screen.getByLabelText(/include page numbers/i));
    expect(screen.getByLabelText(/include page numbers/i)).toBeChecked();

    const props = { onOpenChange: vi.fn(), documentId: "d1", includedCount: 1, excludedCount: 0 };
    rerender(<ExportDialog open={false} {...props} />);
    rerender(<ExportDialog open {...props} />);
    expect(screen.getByLabelText(/include page numbers/i)).not.toBeChecked();
  });

  it("sends the flag on the linked PDF too once checked", async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch();
    open();
    await user.click(screen.getByLabelText(/include page numbers/i));
    await user.click(screen.getByRole("button", { name: "Export to linked PDF" }));
    expect(fetchSpy.mock.calls[0][0]).toContain("/export/pdf");
    expect(body(fetchSpy).includePageNumbers).toBe(true);
  });
});
