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
