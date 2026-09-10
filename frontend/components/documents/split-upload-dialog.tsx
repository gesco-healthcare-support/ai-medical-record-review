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
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
}>) {
  const aggregate = useAggregateDocuments();
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  function reset() {
    setName("");
    setFiles([]);
  }
  /** Dismissing clears the staged files, whichever control did it. Escape and the overlay went
   *  through the Dialog's own `onOpenChange` (which resets) while the Cancel BUTTON called the
   *  parent's setter directly and skipped it, so the same dismissal kept or cleared the list
   *  depending on which one the reviewer used - and a Cancel left files staged to be combined into
   *  the next record. One function, so the two cannot drift again (#264's shape). */
  function close() {
    reset();
    onOpenChange(false);
  }
  function addFiles(list: FileList | null) {
    if (!list) return;
    // Say what was dropped. This used to filter silently, so picking three files and getting two
    // listed left the reviewer to spot which one was missing - and these files are joined into ONE
    // record, so an unnoticed drop is content absent from a deliverable. The single-file path in
    // `DocumentsView` already announces the same rejection; two copies of `isPdf` and only one of
    // them reported the result. The non-PDFs are dropped either way: the picker is not the place to
    // argue about a file type, and keeping the good ones costs the reviewer nothing.
    const picked = Array.from(list);
    const pdfs = picked.filter(isPdf);
    const rejected = picked.length - pdfs.length;
    if (rejected > 0) {
      toast.error(
        rejected === 1
          ? "Only PDF files can be combined. One file was not added."
          : `Only PDF files can be combined. ${rejected} files were not added.`,
      );
    }
    setFiles((prev) => [...prev, ...pdfs]);
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
      close();
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not combine the records." }));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else close();
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
            onClick={close}
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
