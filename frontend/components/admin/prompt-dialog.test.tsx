import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/hooks/use-admin", () => ({ useSavePrompt: vi.fn(), useRevertPrompt: vi.fn() }));
vi.mock("@/lib/admin-api", () => ({ getPrompt: vi.fn() }));

import { ApiError } from "@/lib/api";
import { getPrompt, type PromptInfo } from "@/lib/admin-api";
import { useRevertPrompt, useSavePrompt } from "@/hooks/use-admin";
import { PromptDialog } from "@/components/admin/prompt-dialog";

function withClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** GET /admin/prompts/{id} as the dialog sees it. */
function promptInfo(over: Partial<PromptInfo> = {}): PromptInfo {
  return {
    category_id: "3",
    text: "CUSTOM PROMPT",
    effective_text: "CUSTOM PROMPT",
    builtin_text: "BUILT-IN PROMPT FROM THE APP",
    custom: true,
    ...over,
  };
}

function mockHooks({
  save = vi.fn(),
  revert = vi.fn(),
}: { save?: () => unknown; revert?: () => unknown } = {}) {
  vi.mocked(useSavePrompt).mockReturnValue({
    mutateAsync: save,
    isPending: false,
  } as unknown as ReturnType<typeof useSavePrompt>);
  vi.mocked(useRevertPrompt).mockReturnValue({
    mutateAsync: revert,
    isPending: false,
  } as unknown as ReturnType<typeof useRevertPrompt>);
}

function open() {
  withClient(
    <PromptDialog open onOpenChange={vi.fn()} category={{ id: "3", name: "Imaging" } as never} />,
  );
}

describe("PromptDialog error handling", () => {
  it("shows a humanized message when saving the prompt fails", async () => {
    const user = userEvent.setup();
    vi.mocked(getPrompt).mockResolvedValue(promptInfo({ text: "hi", effective_text: "hi" }));
    mockHooks({ save: vi.fn().mockRejectedValue(new ApiError("network", 0)) });

    open();
    await user.click(await screen.findByRole("button", { name: "Save prompt" }));
    expect(await screen.findByText(/couldn't reach the server/i)).toBeInTheDocument();
  });
});

describe("PromptDialog editing room", () => {
  it("opens wide and gives the prompt a tall wrapping editor", async () => {
    vi.mocked(getPrompt).mockResolvedValue(promptInfo({ text: "a".repeat(4000) }));
    mockHooks();

    open();
    // ev-dialog-wide carries the width + the height ceiling; ev-mono now wraps instead of
    // demanding horizontal scrolling (both live in evaluators-ds.css).
    expect(await screen.findByRole("dialog")).toHaveClass("ev-dialog-wide");
    const editor = await screen.findByLabelText(/prompt sent to the model/i);
    expect(editor).toHaveClass("ev-mono");
    expect(editor).toHaveAttribute("rows", "20");
  });
});

describe("PromptDialog built-in vs custom", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says the built-in prompt is in use and offers no revert", async () => {
    vi.mocked(getPrompt).mockResolvedValue(
      promptInfo({ text: null, effective_text: "BUILT-IN PROMPT FROM THE APP", custom: false }),
    );
    mockHooks();

    open();
    expect(await screen.findByText(/built-in prompt that ships with the app/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /revert to built-in/i })).not.toBeInTheDocument();
    // Nothing to compare against, so the reference panel stays hidden.
    expect(screen.queryByText(/would use without the custom one/i)).not.toBeInTheDocument();
    expect(await screen.findByLabelText(/prompt sent to the model/i)).toHaveValue(
      "BUILT-IN PROMPT FROM THE APP",
    );
  });

  it("shows the built-in alongside a custom prompt and reverts on confirm", async () => {
    const user = userEvent.setup();
    const revert = vi.fn().mockResolvedValue(promptInfo({ text: null, custom: false }));
    vi.mocked(getPrompt).mockResolvedValue(promptInfo());
    mockHooks({ revert });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    open();
    expect(await screen.findByText(/would use without the custom one/i)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /revert to built-in/i }));
    expect(revert).toHaveBeenCalledWith("3");
  });

  it("does not revert when the confirm is dismissed", async () => {
    const user = userEvent.setup();
    const revert = vi.fn();
    vi.mocked(getPrompt).mockResolvedValue(promptInfo());
    mockHooks({ revert });
    vi.spyOn(window, "confirm").mockReturnValue(false);

    open();
    await user.click(await screen.findByRole("button", { name: /revert to built-in/i }));
    expect(revert).not.toHaveBeenCalled();
  });
});
