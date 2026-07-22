"use client";

import { useRef, useState } from "react";
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
import { humanizeError } from "@/lib/errors";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** Combine several pre-split PDFs into one record (POST /api/documents/aggregate). A record name
 *  and the files (joined in listed order) are sent; the backend enqueues classification. */
export function SplitUploadDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const aggregate = useAggregateDocuments();
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  function reset() {
    setName("");
    setFiles([]);
  }
  function addFiles(list: FileList | null) {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list).filter(isPdf)]);
    if (fileInput.current) fileInput.current.value = "";
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
      await aggregate.mutateAsync({ name, files });
      toast.success("Records combined and uploaded.");
      reset();
      onOpenChange(false);
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not combine the records." }));
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
            Combine several pre-split PDFs into one record. Files are joined in the order listed.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="splitName">
              Record name
            </label>
            <input
              id="splitName"
              className="ev-inp"
              placeholder="e.g. the patient or case name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="grid gap-1.5">
            <span className="ev-lbl">Files</span>
            {files.length === 0 ? (
              <div className="rounded-md border-[1.5px] border-dashed border-gray-300 px-3 py-4 text-center text-sm text-muted-foreground">
                No files yet. Add at least two PDFs.
              </div>
            ) : (
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
            )}
            <div>
              <button
                type="button"
                className="ev-btn ev-btn-outline"
                onClick={() => fileInput.current?.click()}
              >
                Add PDFs
              </button>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf"
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>
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
