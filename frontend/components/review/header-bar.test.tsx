import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/review-api", () => ({ saveHeader: vi.fn(), extractHeader: vi.fn() }));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { extractHeader, saveHeader } from "@/lib/review-api";
import { HeaderBar } from "@/components/review/header-bar";

const mockToast = vi.mocked(toast);

afterEach(() => vi.clearAllMocks());

describe("HeaderBar error handling", () => {
  it("toasts a humanized 404 when auto-fill fails", async () => {
    const user = userEvent.setup();
    vi.mocked(extractHeader).mockRejectedValue(new ApiError("not found", 404));
    render(<HeaderBar documentId="d1" header={null} onSaved={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Auto-fill" }));
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(expect.stringMatching(/no longer available/i)),
    );
  });

  it("toasts a humanized network error when saving the header fails", async () => {
    const user = userEvent.setup();
    vi.mocked(saveHeader).mockRejectedValue(new ApiError("network", 0));
    render(<HeaderBar documentId="d1" header={null} onSaved={vi.fn()} />);
    await user.type(screen.getByPlaceholderText("First"), "Jane"); // dirties the form -> Save enables
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});

describe("HeaderBar persistence", () => {
  const DETECTED = {
    patient_first_name: "Jane",
    patient_last_name: "Roe",
    patient_dob: "01/02/1990",
    law_firm: "Acme LLP",
  };

  it("persists on Auto-fill: reports the detected header via onSaved without a separate Save", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.mocked(extractHeader).mockResolvedValue(DETECTED);
    render(<HeaderBar documentId="d1" header={null} onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: "Auto-fill" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(DETECTED));
    expect(mockToast.success).toHaveBeenCalledWith(expect.stringMatching(/detected and saved/i));
    expect(saveHeader).not.toHaveBeenCalled(); // detect alone persists; no manual Save needed
  });

  it("labels the button Re-detect once a header value is present", () => {
    render(
      <HeaderBar
        documentId="d1"
        header={{ ...DETECTED, patient_last_name: "", patient_dob: "", law_firm: "" }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Re-detect" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto-fill" })).toBeNull();
  });
});
