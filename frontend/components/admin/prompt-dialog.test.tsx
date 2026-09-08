import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@/hooks/use-admin", () => ({ useSavePrompt: vi.fn(), useRevertPrompt: vi.fn() }));
vi.mock("@/lib/admin-api", () => ({ getPrompt: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { toast } from "sonner";

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

describe("PromptDialog reopen and dismissal", () => {
  it("shows the server's prompt on reopen, not a leftover unsaved draft", async () => {
    // DEMONSTRATES the bug. PromptDialog is never unmounted - only the inner Radix Dialog toggles -
    // so `text` survives a close. The old sync was keyed on `data` alone, and React Query hands
    // back the SAME object reference within its 30s staleTime when the content is unchanged, so the
    // effect never fired again and the stale draft looked like the prompt on the server. Save would
    // then have written it.
    const user = userEvent.setup();
    vi.mocked(getPrompt).mockResolvedValue(
      promptInfo({ text: "ON THE SERVER", effective_text: "ON THE SERVER" }),
    );
    mockHooks();

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const dialog = (isOpen: boolean) => (
      <QueryClientProvider client={client}>
        <PromptDialog
          open={isOpen}
          onOpenChange={vi.fn()}
          category={{ id: "3", name: "Imaging" } as never}
        />
      </QueryClientProvider>
    );

    const { rerender } = render(dialog(true));
    // Wait for the fetch to land: the textarea is `disabled` while loading, and user-event refuses
    // to type into a disabled element.
    await screen.findByDisplayValue("ON THE SERVER");
    const box = await screen.findByLabelText(/prompt sent to the model/i);
    await user.clear(box);
    await user.type(box, "UNSAVED DRAFT");
    expect(box).toHaveValue("UNSAVED DRAFT");

    rerender(dialog(false)); // closed without saving - the component stays mounted
    rerender(dialog(true)); // reopened, same category, cached data, same reference

    expect(await screen.findByLabelText(/prompt sent to the model/i)).toHaveValue("ON THE SERVER");
  });

  it("closes on Escape even while a save is in flight", async () => {
    // #264 REVERSES what this used to pin. Refusing the close left Escape, an overlay click and
    // the corner button all silently doing nothing while the close button still rendered as
    // active, so a hung save trapped the reviewer with no explanation. The failure the guard was
    // reaching for is now toasted instead, and `Toaster` lives in the root layout, so it outlives
    // this dialog.
    const user = userEvent.setup();
    vi.mocked(getPrompt).mockResolvedValue(promptInfo());
    const onOpenChange = vi.fn();
    vi.mocked(useSavePrompt).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: true, // a save is in flight
    } as unknown as ReturnType<typeof useSavePrompt>);
    vi.mocked(useRevertPrompt).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRevertPrompt>);

    withClient(
      <PromptDialog
        open
        onOpenChange={onOpenChange}
        category={{ id: "3", name: "Imaging" } as never}
      />,
    );
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("toasts a failed save, so a dismissal cannot swallow it", async () => {
    // The half that makes allowing the dismissal safe. Fails on origin/main, where the message
    // existed only in `error` state rendered inside the dialog that was closing.
    const user = userEvent.setup();
    vi.mocked(toast.error).mockClear();
    vi.mocked(getPrompt).mockResolvedValue(promptInfo({ text: "hi", effective_text: "hi" }));
    mockHooks({ save: vi.fn().mockRejectedValue(new ApiError("network", 0)) });

    open();
    await user.click(await screen.findByRole("button", { name: "Save prompt" }));

    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/couldn.t reach the server/i));
  });
});

describe("PromptDialog when the prompt could not be loaded", () => {
  // #263. `data` is undefined both while the query is IN FLIGHT and once it has FAILED, and
  // `isLoading` is false in the second case - so the reset to "" rendered an empty EDITABLE box
  // with Save enabled, which reads as "this category has no custom prompt" when the truth is "we
  // could not load it". All three assertions below fail on origin/main.
  async function openFailing() {
    vi.mocked(getPrompt).mockRejectedValue(new ApiError("network", 0));
    mockHooks();
    open();
    return screen.findByText(/could not be loaded/i);
  }

  it("says so, rather than asserting which prompt is in use", async () => {
    await openFailing();
    // The two normal sentences both CLAIM which prompt this category uses, and on a failed fetch
    // we do not know - so neither may appear.
    expect(screen.queryByText(/built-in prompt that ships with the app/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/uses a custom prompt saved here/i)).not.toBeInTheDocument();
  });

  it("leaves the empty editor read-only", async () => {
    await openFailing();
    expect(screen.getByLabelText(/prompt sent to the model/i)).toBeDisabled();
  });

  it("does not offer to save nothing over the prompt on the server", async () => {
    await openFailing();
    expect(screen.getByRole("button", { name: "Save prompt" })).toBeDisabled();
  });
});
