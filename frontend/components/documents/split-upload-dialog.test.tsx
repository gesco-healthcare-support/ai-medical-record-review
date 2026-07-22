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
