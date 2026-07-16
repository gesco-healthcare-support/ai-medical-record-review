"use client";

import { useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAggregateDocuments } from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** Combine several pre-split PDFs into one record (POST /api/documents/aggregate). Files are
 *  joined in the listed order; the backend names the record and enqueues classification. */
export function SplitUploadDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const aggregate = useAggregateDocuments();
  const [files, setFiles] = useState<File[]>([]);

  function reset() {
    setFiles([]);
  }
  function addFiles(list: FileList | null) {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list).filter(isPdf)]);
  }
  function removeAt(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function submit() {
    if (files.length < 2) {
      toast.error("Add at least two PDFs to combine.");
      return;
    }
    try {
      await aggregate.mutateAsync(files);
      toast.success("Records combined and uploaded.");
      reset();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not combine the records.");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload split records</DialogTitle>
          <DialogDescription>
            Combine several pre-split PDFs into one record. They are joined in the order listed.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <input
            type="file"
            accept="application/pdf"
            multiple
            className="ev-inp"
            onChange={(e) => addFiles(e.target.files)}
          />
          {files.length > 0 ? (
            <ul className="grid gap-1.5">
              {files.map((file, index) => (
                <li
                  key={`${file.name}-${index}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-gray-200 px-3 py-2 text-sm"
                >
                  <span className="truncate">{file.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    className="text-gray-400 hover:text-danger"
                    onClick={() => removeAt(index)}
                  >
                    <X className="size-4" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <DialogFooter>
          <button
            type="button"
            className="ev-btn ev-btn-outline"
            onClick={() => onOpenChange(false)}
            disabled={aggregate.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={submit}
            disabled={aggregate.isPending || files.length < 2}
          >
            {aggregate.isPending ? "Combining..." : "Combine & upload"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
