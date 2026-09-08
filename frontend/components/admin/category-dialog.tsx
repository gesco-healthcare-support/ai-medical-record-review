"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AdminCategory, CategoryInput } from "@/lib/admin-api";
import { humanizeError } from "@/lib/errors";

/** Add / edit a category. The id is entered only when creating (immutable - it keys existing
 *  records), so it is disabled while editing. Business rules (numeric id, duplicate, empty name)
 *  are enforced server-side and surfaced as the footer error. */
export function CategoryDialog({
  open,
  onOpenChange,
  editing,
  onCreate,
  onUpdate,
  saving,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: AdminCategory | null;
  onCreate: (body: CategoryInput & { id: string }) => Promise<void>;
  onUpdate: (id: string, body: Partial<CategoryInput>) => Promise<void>;
  saving: boolean;
}>) {
  const creating = !editing;
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [examples, setExamples] = useState("");
  const [autoAssign, setAutoAssign] = useState(true);
  const [summarizeDefault, setSummarizeDefault] = useState(true);
  const [active, setActive] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setId(editing?.id ?? "");
    setName(editing?.name ?? "");
    setDescription(editing?.description ?? "");
    setExamples((editing?.examples ?? []).join("\n"));
    setAutoAssign(editing ? editing.auto_assign : true);
    setSummarizeDefault(editing ? editing.summarize_default : true);
    setActive(editing ? editing.active : true);
  }, [open, editing]);

  async function save() {
    setError("");
    const body: CategoryInput = {
      name: name.trim(),
      description: description.trim(),
      examples: examples
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
      auto_assign: autoAssign,
      summarize_default: summarizeDefault,
      active,
    };
    try {
      if (creating) await onCreate({ ...body, id: id.trim() });
      else if (editing) await onUpdate(editing.id, body);
      onOpenChange(false);
    } catch (err) {
      // BOTH, for the reason in the `Dialog` comment below: inline so a reviewer still looking
      // at the form sees it beside the fields, and a toast because `Toaster` lives in the root
      // layout and therefore outlives this dialog. That pairing is what makes refusing a
      // mid-save dismissal unnecessary (#264) - the failure can no longer be swallowed.
      const message = humanizeError(err, { fallback: "Could not save the category." });
      setError(message);
      toast.error(message);
    }
  }

  return (
    // NOT guarded against a mid-save dismissal, and #264 is why that guard was wrong. Refusing
    // the close left Escape, an overlay click and the corner button all silently doing nothing
    // while the close button still rendered as active, so a hung save trapped the reviewer in
    // the modal with no explanation. The real defect was the failure being written ONLY into
    // `error` state rendered inside a dialog that was closing; the toast in `save()` above
    // fixes that at the source, so the dismissal can be allowed.
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Wide: at the 384px default the description and the examples list are unusably cramped. */}
      <DialogContent className="ev-dialog-wide">
        <DialogHeader>
          <DialogTitle>{creating ? "Add category" : `Edit category ${editing?.id}`}</DialogTitle>
          <DialogDescription>
            Category ids are permanent - they key existing records.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="flex gap-3">
            <div className="grid flex-1 gap-1.5">
              <label className="ev-lbl" htmlFor="catId">
                ID (number)
              </label>
              <input
                id="catId"
                className="ev-inp"
                inputMode="numeric"
                placeholder="e.g. 15"
                value={id}
                disabled={!creating}
                onChange={(e) => setId(e.target.value)}
              />
            </div>
            <div className="grid flex-[2] gap-1.5">
              <label className="ev-lbl" htmlFor="catName">
                Name
              </label>
              <input
                id="catName"
                className="ev-inp"
                placeholder="Category name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="catDescription">
              Description
            </label>
            <input
              id="catDescription"
              className="ev-inp"
              placeholder="What documents belong here"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="catExamples">
              Example document titles (one per line)
            </label>
            <textarea
              id="catExamples"
              className="ev-inp"
              rows={4}
              placeholder={"MRI Report\nCT Scan"}
              value={examples}
              onChange={(e) => setExamples(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="ev-check">
              <input
                type="checkbox"
                checked={autoAssign}
                onChange={(e) => setAutoAssign(e.target.checked)}
              />{" "}
              Auto-assign (classifier may pick this)
            </label>
            <label className="ev-check">
              <input
                type="checkbox"
                checked={summarizeDefault}
                onChange={(e) => setSummarizeDefault(e.target.checked)}
              />{" "}
              Summarize by default
            </label>
            <label className="ev-check">
              <input
                type="checkbox"
                checked={active}
                onChange={(e) => setActive(e.target.checked)}
              />{" "}
              Active
            </label>
          </div>
        </div>
        <DialogFooter>
          {error ? <span className="error-text mr-auto">{error}</span> : null}
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </button>
          <button type="button" className="ev-btn ev-btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
