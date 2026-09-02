"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "prompt", id],
    queryFn: () => getPrompt(id),
    enabled: open && Boolean(id),
  });

  const isCustom = Boolean(data?.custom);
  const builtinText = data?.builtin_text ?? "";

  useEffect(() => {
    if (open) setError("");
  }, [open]);

  useEffect(() => {
    if (data) setText(data.text ?? data.effective_text ?? "");
  }, [data]);

  async function submit() {
    setError("");
    try {
      await save.mutateAsync({ id, text });
      onOpenChange(false);
    } catch (err) {
      setError(humanizeError(err, { fallback: "Could not save the prompt." }));
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
      setError(humanizeError(err, { fallback: "Could not revert the prompt." }));
    }
  }

  const busy = save.isPending || revert.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="ev-dialog-wide">
        <DialogHeader>
          <DialogTitle>Summary prompt{category ? ` - ${category.name}` : ""}</DialogTitle>
          <DialogDescription>
            {isCustom
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
            disabled={isLoading}
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
            disabled={busy || isLoading}
          >
            {save.isPending ? "Saving..." : "Save prompt"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
