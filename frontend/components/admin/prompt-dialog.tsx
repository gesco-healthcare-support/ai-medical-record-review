"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getPrompt, type AdminCategory } from "@/lib/admin-api";
import { useRevertPrompt, useSavePrompt } from "@/hooks/use-admin";
import { humanizeError } from "@/lib/errors";

/** Edit a category's summary prompt (wide dialog + monospace textarea).
 *
 *  Prompts live in the app code and ship with a deploy; saving here creates a CUSTOM prompt that
 *  overrides the built-in one for this category until it is reverted. When a custom prompt exists the
 *  built-in is shown read-only alongside it, so the difference is visible before reverting. */
export function PromptDialog({
  open,
  onOpenChange,
  category,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  category: AdminCategory | null;
}>) {
  const id = category?.id ?? "";
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const save = useSavePrompt();
  const revert = useRevertPrompt();

  // `isError` is read, and #263 is why. `data` is undefined both while the query is in flight AND
  // once it has FAILED, and `isLoading` is false in the second case - so the reset below
  // rendered an empty EDITABLE box on a failed fetch, which reads as "this category has no
  // custom prompt" when
  // the truth is "we could not load it". The whole point of #262 was the dialog not misrepresenting
  // server state, and that branch did exactly that.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "prompt", id],
    queryFn: () => getPrompt(id),
    enabled: open && Boolean(id),
  });

  const isCustom = Boolean(data?.custom);
  const builtinText = data?.builtin_text ?? "";

  useEffect(() => {
    if (open) setError("");
  }, [open]);

  // Keyed on `open` as well as `data`, which is the whole of this fix. `PromptDialog` is never
  // unmounted - only the inner Radix `Dialog` toggles - so `text` survives a close, and a `[data]`
  // key alone does not fire on reopen: the query is keyed by category id, the client's `staleTime`
  // is 30s, and structural sharing hands back the SAME object reference when the refetched content
  // is unchanged. So reopening the same category showed a leftover unsaved draft as if it were the
  // prompt on the server - and Save would then write it.
  //
  // `CategoryDialog` next door already resets every field in an `[open, editing]`-keyed effect for
  // exactly this reason; this makes the two agree.
  //
  // Resetting to "" when there is no `data` covers TWO states - still loading, and failed - and
  // that is safe only because the textarea is disabled in both (`isLoading || isError` below). #263
  // caught the version where it was disabled on the first and editable on the second, so a failed
  // fetch rendered an empty writable box that read as "this category has no custom prompt".
  useEffect(() => {
    if (!open) return;
    setText(data ? (data.text ?? data.effective_text ?? "") : "");
  }, [open, data]);

  async function submit() {
    setError("");
    try {
      await save.mutateAsync({ id, text });
      onOpenChange(false);
    } catch (err) {
      // BOTH: inline so a reviewer still looking at the dialog sees it beside the field they were
      // editing, and a toast because `Toaster` lives in the root layout and therefore survives this
      // dialog closing. That pairing is what makes trapping the dialog open unnecessary (#264) - a
      // mid-save dismissal can no longer swallow the failure.
      const message = humanizeError(err, { fallback: "Could not save the prompt." });
      setError(message);
      toast.error(message);
    }
  }

  async function revertToBuiltIn() {
    setError("");
    if (
      !window.confirm(
        "Discard this custom prompt? The category goes back to the built-in prompt that ships with the app.",
      )
    ) {
      return;
    }
    try {
      await revert.mutateAsync(id); // the hook refetches this category's prompt + the list
      onOpenChange(false);
    } catch (err) {
      const message = humanizeError(err, { fallback: "Could not revert the prompt." });
      setError(message);
      toast.error(message);
    }
  }

  const busy = save.isPending || revert.isPending;

  return (
    // NOT guarded against a mid-save dismissal, and #264 is why the guard was wrong. Refusing the
    // close left Escape, an overlay click and the corner button all silently doing nothing
    // while the close button still looked active - so a hung save trapped the reviewer in the
    // modal with no
    // explanation, which is a worse failure than the one it fixed. The failure is surfaced by the
    // toast in the catch blocks above instead, which outlives this dialog.
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="ev-dialog-wide">
        <DialogHeader>
          <DialogTitle>Summary prompt{category ? ` - ${category.name}` : ""}</DialogTitle>
          <DialogDescription>
            {/* The error case comes FIRST, because both other sentences assert which prompt this
                category is using - and on a failed fetch we do not know. Stating "uses the built-in
                prompt" when the request failed is the same misrepresentation as the empty editable
                box #263 is about, one line further up. */}
            {isError
              ? "This category's prompt could not be loaded, so nothing here reflects the server. Close and try again."
              : isCustom
                ? "This category uses a custom prompt saved here, which overrides the built-in one."
                : "This category uses the built-in prompt that ships with the app. Saving creates a custom prompt that overrides it until you revert."}
          </DialogDescription>
        </DialogHeader>

        {isCustom && builtinText ? (
          <div className="ev-refpanel">
            <div className="ev-refpanel-head">
              <span>Built-in prompt this category would use without the custom one</span>
              <button
                type="button"
                className="ev-btn ev-btn-ghost ev-btn-sm"
                onClick={() => setText(builtinText)}
              >
                Copy into editor
              </button>
            </div>
            <pre className="ev-mono ev-refpanel-body">{builtinText}</pre>
          </div>
        ) : null}

        <div className="grid gap-1.5">
          <label className="ev-lbl" htmlFor="promptText">
            Prompt sent to the model for this category
          </label>
          <textarea
            id="promptText"
            className="ev-inp ev-mono"
            rows={20}
            value={text}
            disabled={isLoading || isError}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <DialogFooter>
          <span className="muted mr-auto text-[12.5px]">
            Applies to summaries written after saving; existing summaries keep their text until
            re-run.
          </span>
          {error ? <span className="error-text">{error}</span> : null}
          {isCustom ? (
            <button
              type="button"
              className="ev-btn ev-btn-ghost"
              onClick={revertToBuiltIn}
              disabled={busy}
            >
              {revert.isPending ? "Reverting..." : "Revert to built-in"}
            </button>
          ) : null}
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={submit}
            disabled={busy || isLoading || isError}
          >
            {save.isPending ? "Saving..." : "Save prompt"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
