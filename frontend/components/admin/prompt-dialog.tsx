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
import { useSavePrompt } from "@/hooks/use-admin";

// The general summary prompt lives under category 100 (catalog.get_prompt falls back to it); we read
// it via the existing endpoint so the reference panel can show what a category would otherwise use.
const GENERAL_CATEGORY_ID = "100";

/** Edit a category's summary prompt (wide dialog + monospace textarea). Shows the general prompt the
 *  category would otherwise inherit as a read-only reference, with a one-click revert. The current
 *  prompt is fetched on open; saving creates/updates a custom prompt for the category. */
export function PromptDialog({
  open,
  onOpenChange,
  category,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  category: AdminCategory | null;
}) {
  const id = category?.id ?? "";
  const isGeneral = id === GENERAL_CATEGORY_ID;
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const save = useSavePrompt();

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "prompt", id],
    queryFn: () => getPrompt(id),
    enabled: open && Boolean(id),
  });

  // The general (category-100) prompt, for the reference panel + revert (skip when editing 100).
  const { data: general } = useQuery({
    queryKey: ["admin", "prompt", GENERAL_CATEGORY_ID],
    queryFn: () => getPrompt(GENERAL_CATEGORY_ID),
    enabled: open && Boolean(id) && !isGeneral,
  });
  const generalText = general?.effective_text ?? "";

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
      setError(err instanceof Error ? err.message : "Could not save the prompt.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="ev-dialog-wide">
        <DialogHeader>
          <DialogTitle>Summary prompt{category ? ` - ${category.name}` : ""}</DialogTitle>
          <DialogDescription>
            {data?.custom
              ? "This category uses a custom summary prompt."
              : "This category is inheriting the general prompt; saving creates a custom one for it."}
          </DialogDescription>
        </DialogHeader>

        {!isGeneral && generalText ? (
          <div className="ev-refpanel">
            <div className="ev-refpanel-head">
              <span>General prompt this category would otherwise use</span>
              <button
                type="button"
                className="ev-btn ev-btn-ghost ev-btn-sm"
                onClick={() => setText(generalText)}
              >
                Revert editor to this
              </button>
            </div>
            <pre className="ev-mono ev-refpanel-body">{generalText}</pre>
          </div>
        ) : null}

        <div className="grid gap-1.5">
          <label className="ev-lbl" htmlFor="promptText">
            Prompt sent to the model for this category
          </label>
          <textarea
            id="promptText"
            className="ev-inp ev-mono"
            rows={14}
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
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => onOpenChange(false)}
            disabled={save.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={submit}
            disabled={save.isPending || isLoading}
          >
            {save.isPending ? "Saving..." : "Save prompt"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
