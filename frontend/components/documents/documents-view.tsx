"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  useDeleteDocument,
  useDocuments,
  useStartIdentification,
  useUploadDocument,
} from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";
import type { DocumentListItem } from "@/lib/types";
import { DocumentsTable } from "./documents-table";
import { EmptyState } from "./empty-state";
import { SplitUploadDialog } from "./split-upload-dialog";
import { ConfirmDialog } from "./confirm-dialog";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}
function errMessage(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

/** My documents: the documents table + upload (button / browse / drag-drop) + first-run empty
 *  state. Upload does NOT start identification (a mis-clicked file must not spend model quota). */
export function DocumentsView() {
  const router = useRouter();
  const { data: docs = [], isLoading } = useDocuments();
  const upload = useUploadDocument();
  const del = useDeleteDocument();
  const identify = useStartIdentification();

  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [splitOpen, setSplitOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const [reidentifyTarget, setReidentifyTarget] = useState<DocumentListItem | null>(null);

  function pickFile() {
    fileInput.current?.click();
  }

  async function uploadFile(file: File | undefined) {
    if (!file) return;
    if (!isPdf(file)) {
      toast.error("Only PDF files can be uploaded.");
      return;
    }
    try {
      const created = await upload.mutateAsync(file);
      if (created.sha256_duplicate) {
        toast("You already uploaded an identical file. Continuing anyway.");
      } else {
        toast.success("Record uploaded.");
      }
    } catch (err) {
      toast.error(errMessage(err, "Upload failed."));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function runIdentify(id: string) {
    try {
      await identify.mutateAsync(id);
      toast.success("Identification started.");
    } catch (err) {
      toast.error(errMessage(err, "Could not start identification."));
    }
  }

  async function runDelete(id: string) {
    try {
      await del.mutateAsync(id);
      toast.success("Record deleted.");
    } catch (err) {
      toast.error(errMessage(err, "Could not delete the record."));
    }
  }

  function onIdentify(doc: DocumentListItem) {
    if (doc.rows_count) {
      setReidentifyTarget(doc); // re-run replaces corrections -> confirm first
    } else {
      void runIdentify(doc.id);
    }
  }

  return (
    <main
      className="flex flex-1 flex-col"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void uploadFile(e.dataTransfer.files[0]);
      }}
    >
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => void uploadFile(e.target.files?.[0])}
      />

      {isLoading ? null : docs.length === 0 ? (
        <EmptyState dragging={dragging} uploading={upload.isPending} onBrowse={pickFile} />
      ) : (
        <section className="hd-column">
          <div className="hd-header">
            <h1>My documents</h1>
            <div className="flex flex-wrap gap-2.5">
              <button
                type="button"
                className="ev-btn ev-btn-outline ev-btn-lg"
                onClick={() => setSplitOpen(true)}
              >
                Upload split records
              </button>
              <button
                type="button"
                className="ev-btn ev-btn-primary ev-btn-lg"
                onClick={pickFile}
                disabled={upload.isPending}
              >
                {upload.isPending ? "Uploading..." : "Upload a record"}
              </button>
            </div>
          </div>
          <DocumentsTable
            docs={docs}
            onOpen={(id) => router.push(`/records/${id}`)}
            onIdentify={onIdentify}
            onDelete={(doc) => setDeleteTarget(doc)}
          />
        </section>
      )}

      <SplitUploadDialog open={splitOpen} onOpenChange={setSplitOpen} />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete this record?"
        description="This deletes the record and all of its rows and summaries. This cannot be undone."
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          if (deleteTarget) void runDelete(deleteTarget.id);
          setDeleteTarget(null);
        }}
      />

      <ConfirmDialog
        open={Boolean(reidentifyTarget)}
        onOpenChange={(open) => !open && setReidentifyTarget(null)}
        title="Re-run identification?"
        description="Re-running identification replaces the current document list and your corrections. Continue?"
        confirmLabel="Re-run"
        onConfirm={() => {
          if (reidentifyTarget) void runIdentify(reidentifyTarget.id);
          setReidentifyTarget(null);
        }}
      />
    </main>
  );
}
