"use client";

import { useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAssignDocument } from "@/hooks/use-documents";
import { humanizeError, isDuplicateRecord } from "@/lib/errors";
import type { AdminAccount, DocumentListItem } from "@/lib/types";

/** Admins: move a record into another reviewer's account, so that reviewer works on it.
 *
 *  One owner at a time - the record leaves the list it is in and shows in the chosen reviewer's.
 *  When that reviewer already holds the same file the server refuses once with the reason, and the
 *  dialog shows it and turns its button into "Assign anyway" rather than stacking a second dialog.
 *  Choosing a different reviewer clears that state: the reason was about the previous choice. */
export function AssignDialog({
  doc,
  accounts,
  currentOwnerId,
  onClose,
}: Readonly<{
  doc: DocumentListItem | null;
  accounts: AdminAccount[];
  currentOwnerId: number | undefined;
  onClose: () => void;
}>) {
  const assign = useAssignDocument();
  const [choice, setChoice] = useState("");
  const [duplicate, setDuplicate] = useState<string | null>(null);
  const others = accounts.filter((a) => a.id !== currentOwnerId);
  const chosen = others.find((a) => String(a.id) === choice);

  function close() {
    setChoice("");
    setDuplicate(null);
    onClose();
  }

  async function submit() {
    if (!doc || !chosen) return;
    const name = chosen.name || chosen.email;
    try {
      await assign.mutateAsync({ id: doc.id, owner: chosen.id, allowDuplicate: duplicate !== null });
      toast.success(`Record assigned to ${name}.`);
      close();
    } catch (err) {
      if (isDuplicateRecord(err)) {
        setDuplicate(err.message);
        return;
      }
      toast.error(
        humanizeError(err, {
          fallback: "Could not assign the record.",
          notFound: "That record or reviewer is no longer available. Refresh the list.",
        }),
      );
    }
  }

  let action = "Assign";
  if (assign.isPending) action = "Assigning...";
  else if (duplicate) action = "Assign anyway";

  return (
    <Dialog open={Boolean(doc)} onOpenChange={(next) => !next && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign this record</DialogTitle>
          <DialogDescription>
            Move &quot;{doc?.original_filename}&quot; to another reviewer. It leaves this list and
            shows in theirs.
          </DialogDescription>
        </DialogHeader>

        <label className="grid gap-1.5">
          <span className="ev-lbl">Assign to</span>
          <select
            className="ev-inp"
            value={choice}
            onChange={(e) => {
              setChoice(e.target.value);
              setDuplicate(null);
            }}
          >
            <option value="">Choose a reviewer</option>
            {others.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name || a.email}
              </option>
            ))}
          </select>
        </label>

        {duplicate ? (
          <div className="banner" role="alert">
            {duplicate} Assign it anyway?
          </div>
        ) : null}

        <DialogFooter>
          <button
            type="button"
            className="ev-btn ev-btn-outline"
            onClick={close}
            disabled={assign.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={() => void submit()}
            disabled={assign.isPending || !chosen}
          >
            {action}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
