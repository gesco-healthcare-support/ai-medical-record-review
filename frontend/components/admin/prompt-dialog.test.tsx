import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/hooks/use-admin", () => ({ useSavePrompt: vi.fn() }));
vi.mock("@/lib/admin-api", () => ({ getPrompt: vi.fn() }));

import { ApiError } from "@/lib/api";
import { getPrompt } from "@/lib/admin-api";
import { useSavePrompt } from "@/hooks/use-admin";
import { PromptDialog } from "@/components/admin/prompt-dialog";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("PromptDialog error handling", () => {
  it("shows a humanized message when saving the prompt fails", async () => {
    const user = userEvent.setup();
    vi.mocked(getPrompt).mockResolvedValue({
      category_id: "3",
      text: "hi",
      effective_text: "hi",
      custom: true,
    });
    vi.mocked(useSavePrompt).mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue(new ApiError("network", 0)),
      isPending: false,
    } as unknown as ReturnType<typeof useSavePrompt>);

    withClient(
      <PromptDialog
        open
        onOpenChange={vi.fn()}
        category={{ id: "3", name: "Imaging" } as never}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "Save prompt" }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });
});
