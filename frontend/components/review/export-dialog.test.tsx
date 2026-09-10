import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExportDialog } from "@/components/review/export-dialog";

describe("ExportDialog error handling", () => {
  afterEach(() => vi.restoreAllMocks());

  function openDialog() {
    render(
      <ExportDialog
        open
        onOpenChange={vi.fn()}
        documentId="d1"
        includedCount={2}
        excludedCount={0}
      />,
    );
  }

  it("names a transport failure, rather than showing a generic fallback", async () => {
    // CHANGED EXPECTATION, deliberately. This asserted the generic "Export failed." for a rejected
    // fetch, because the dialog's own `fetch` threw a plain Error that `humanizeError` cannot
    // classify. Going through `downloadFile` makes it ApiError(status 0), which is the same shape
    // apiFetch produces - so the export now gets the specific copy every other request already got
    // for the same failure. The old expectation was the defect, not the contract.
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("boom"));
    openDialog();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });

  it("shows the server's own reason for a refusal", async () => {
    // The dialog never read the error body - it threw `export failed (500)` on the status alone -
    // so a 422 naming the document it could not read, or a 503 naming an AI outage, reached the
    // reviewer as "Export failed." and nothing else.
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 422,
      headers: new Headers(),
      json: async () => ({ detail: "One document could not be read." }),
    } as unknown as Response);
    openDialog();
    await user.click(screen.getByRole("button", { name: "Export to Word" }));
    expect(await screen.findByText("One document could not be read.")).toBeInTheDocument();
  });

  it("does not report success when the session has ended", async () => {
    // A 401 used to `return`, so the dialog closed as though the file had been written. It now
    // throws like every other client, and the reviewer is told before the redirect lands.
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Headers(),
      json: async () => ({}),
    } as unknown as Response);
    render(
      <ExportDialog
        open
        onOpenChange={onOpenChange}
        documentId="d1"
        includedCount={2}
        excludedCount={0}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Export to Word" }));

    expect(await screen.findByText(/session has ended/i)).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
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
