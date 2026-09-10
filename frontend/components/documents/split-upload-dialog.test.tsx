import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const aggregate = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({ useAggregateDocuments: () => aggregate }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { SplitUploadDialog } from "@/components/documents/split-upload-dialog";

const pdf = (name: string) => new File([new Uint8Array([1])], name, { type: "application/pdf" });

afterEach(() => vi.clearAllMocks());

describe("SplitUploadDialog error handling", () => {
  it("toasts a humanized message when combining fails", async () => {
    const user = userEvent.setup();
    aggregate.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    render(<SplitUploadDialog open onOpenChange={vi.fn()} />);
    // Two PDFs are required to enable Combine; the Radix dialog portals to document.body.
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [pdf("a.pdf"), pdf("b.pdf")] },
    });
    await user.click(await screen.findByRole("button", { name: "Combine & upload" }));
    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});

describe("SplitUploadDialog file handling", () => {
  const other = (name: string) =>
    new File([new Uint8Array([1])], name, { type: "application/msword" });

  it("says so when a picked file is not a PDF, instead of dropping it silently", async () => {
    // `addFiles` filtered on isPdf and said nothing, so a reviewer who picked three files and got
    // two listed had to notice which one was missing. The single-file path in DocumentsView toasts
    // "Only PDF files can be uploaded." for the same rejection - two copies of one predicate, and
    // only one of them announced the result. Here it combines into ONE record, so an unnoticed drop
    // is content missing from a deliverable.
    render(<SplitUploadDialog open onOpenChange={vi.fn()} />);
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [pdf("a.pdf"), other("b.doc"), pdf("c.pdf")] },
    });

    expect(vi.mocked(toast).error).toHaveBeenCalledWith(expect.stringMatching(/only pdf/i));
    // The two PDFs are still staged - a rejected file must not cost the reviewer the whole pick.
    expect(await screen.findByText("a.pdf")).toBeInTheDocument();
    expect(await screen.findByText("c.pdf")).toBeInTheDocument();
    expect(screen.queryByText("b.doc")).not.toBeInTheDocument();
  });

  it("stays quiet when every picked file is a PDF", async () => {
    render(<SplitUploadDialog open onOpenChange={vi.fn()} />);
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [pdf("a.pdf"), pdf("b.pdf")] },
    });

    expect(await screen.findByText("a.pdf")).toBeInTheDocument();
    expect(vi.mocked(toast).error).not.toHaveBeenCalled();
  });

  it("clears the staged files on Cancel, the way dismissing already did", async () => {
    // Escape and the overlay went through the Dialog's own onOpenChange, which resets; the Cancel
    // BUTTON called the parent's setter directly and skipped it. So the same dismissal cleared the
    // list or kept it depending on which control was used, and a Cancel left files staged to be
    // combined into the next record. Same shape as #264: two dismissal paths, one guard.
    const user = userEvent.setup();
    render(<SplitUploadDialog open onOpenChange={vi.fn()} />);
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [pdf("a.pdf"), pdf("b.pdf")] },
    });
    expect(await screen.findByText("a.pdf")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    // `open` is still true here (the parent's setter is a spy), so the cleared list is visible.
    expect(await screen.findByText(/No files yet/i)).toBeInTheDocument();
    expect(screen.queryByText("a.pdf")).not.toBeInTheDocument();
  });
});
