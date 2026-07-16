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

/** Edit a category's summary prompt (wide dialog + monospace textarea). The current prompt is
 *  fetched on open; if the category has no custom prompt it shows the inherited general text, and
 *  saving creates a custom prompt for the category. */
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
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const save = useSavePrompt();

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "prompt", id],
    queryFn: () => getPrompt(id),
    enabled: open && Boolean(id),
  });

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
              ? "This category has a custom summary prompt."
              : "Inheriting the general prompt; saving creates a custom one for this category."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <label className="ev-lbl" htmlFor="promptText">
            Prompt sent to the model for this category
          </label>
          <textarea
            id="promptText"
            className="ev-inp ev-mono"
            rows={16}
            value={text}
            disabled={isLoading}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <DialogFooter>
          {error ? <span className="error-text mr-auto">{error}</span> : null}
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
