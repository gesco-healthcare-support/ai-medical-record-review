import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const update = { mutateAsync: vi.fn(), isPending: false };
vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: { is_superuser: true }, isLoading: false }),
}));
vi.mock("@/hooks/use-documents", () => ({ useDocuments: () => ({ data: [] }) }));
vi.mock("@/hooks/use-admin", () => ({
  useCategories: () => ({
    data: [
      {
        id: "3",
        name: "Imaging",
        description: "",
        examples: [],
        auto_assign: true,
        active: true,
        has_summary_prompt: false,
      },
    ],
    isLoading: false,
  }),
  useCreateCategory: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCategory: () => update,
  useReprocess: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSavePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/lib/admin-api", () => ({
  getPrompt: vi.fn().mockResolvedValue({ text: "", effective_text: "", custom: false }),
}));

import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { AdminView } from "@/components/admin/admin-view";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe("AdminView error handling", () => {
  it("toasts a humanized message when toggling a category fails", async () => {
    const user = userEvent.setup();
    update.mutateAsync.mockRejectedValue(new ApiError("network", 0));
    withClient(<AdminView />);
    await user.click(await screen.findByRole("button", { name: "Deactivate" }));
    await waitFor(() =>
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't reach the server/i),
      ),
    );
  });
});
