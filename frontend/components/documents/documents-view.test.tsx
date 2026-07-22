import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const upload = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-documents", () => ({
  useDocuments: () => ({ data: [], isLoading: false }),
  useUploadDocument: () => upload,
  useDeleteDocument: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useStartIdentification: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAggregateDocuments: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { DocumentsView } from "@/components/documents/documents-view";

afterEach(() => vi.clearAllMocks());

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
