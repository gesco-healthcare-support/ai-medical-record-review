"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  useDeleteDocument,
  useDocuments,
  useStartIdentification,
  useUploadDocument,
} from "@/hooks/use-documents";
import { useAccounts } from "@/hooks/use-admin";
import { useCurrentUser } from "@/hooks/use-current-user";
import { humanizeError } from "@/lib/errors";
import type { AdminAccount, DocumentListItem } from "@/lib/types";
import { DocumentsTable } from "./documents-table";
import { EmptyState, UPLOAD_INPUT_ID } from "./empty-state";
import { SplitUploadDialog } from "./split-upload-dialog";
import { ConfirmDialog } from "./confirm-dialog";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}
function errMessage(err: unknown, fallback: string) {
  return humanizeError(err, {
    fallback,
    notFound: "That record is no longer available - it may have been deleted. Refresh the list.",
  });
}

// The reviewer an admin last chose, kept for the browser tab so opening a record and coming back
// does not reset the list to the admin's own. Per tab and lost on close, deliberately: this is a
// convenience, and every read and write is guarded because storage can be unavailable.
const OWNER_KEY = "mrr.records.owner";

function readSavedOwner(): number | null {
  try {
    const saved = Number(sessionStorage.getItem(OWNER_KEY));
    return Number.isInteger(saved) && saved > 0 ? saved : null;
  } catch {
    return null;
  }
}

function saveOwner(id: number | null) {
  try {
    if (id === null) sessionStorage.removeItem(OWNER_KEY);
    else sessionStorage.setItem(OWNER_KEY, String(id));
  } catch {
    // Storage unavailable: the choice simply is not remembered.
  }
}

/** Admins only: whose records the page shows. The client's lead reviewer asked to pick one
 *  reviewer and see that reviewer's records, not everyone's together, so he can fix a mistake
 *  after they have left. Starts on the admin's own records. */
export function OwnerPicker({
  meId,
  accounts,
  value,
  onChange,
}: Readonly<{
  meId: number | undefined;
  accounts: AdminAccount[];
  value: number | null;
  onChange: (id: number | null) => void;
}>) {
  const others = accounts.filter((a) => a.id !== meId);
  return (
    <label className="rc-hb-field">
      <span className="ev-lbl">Show records for</span>
      <select
        className="ev-inp"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">My records</option>
        {others.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name || a.email}
          </option>
        ))}
      </select>
    </label>
  );
}

function accountName(accounts: AdminAccount[] | undefined, id: number | null) {
  return accounts?.find((a) => a.id === id)?.name || "this reviewer";
}

/** Whose records the page shows. Everyone but an admin always sees their own; an admin can pick
 *  another reviewer, remembered for the tab. */
function useRecordsOwner() {
  const { data: me } = useCurrentUser();
  const isAdmin = Boolean(me?.is_superuser);
  const accounts = useAccounts(isAdmin);
  const [ownerId, setOwnerId] = useState<number | null>(null);
  useEffect(() => {
    if (isAdmin) setOwnerId(readSavedOwner());
  }, [isAdmin]);
  const choose = (id: number | null) => {
    setOwnerId(id);
    saveOwner(id);
  };
  // Another reviewer's records. Uploading and deleting are left out while viewing them: an
  // upload would land in the admin's own list, not this one, and deleting is not fixing (the
  // server refuses it too).
  const viewingOther = isAdmin && ownerId !== null && ownerId !== me?.id;
  return {
    meId: me?.id,
    isAdmin,
    accounts: accounts.data ?? [],
    ownerId: viewingOther ? ownerId : null,
    choose,
    viewingOther,
    ownerName: viewingOther ? accountName(accounts.data, ownerId) : null,
  };
}

type RecordsOwner = ReturnType<typeof useRecordsOwner>;

/** The admin's picker, and the note while another reviewer's records are showing. */
function OwnerBar({ owner }: Readonly<{ owner: RecordsOwner }>) {
  if (!owner.isAdmin) return null;
  return (
    <section className="hd-column">
      <OwnerPicker
        meId={owner.meId}
        accounts={owner.accounts}
        value={owner.ownerId}
        onChange={owner.choose}
      />
      {owner.viewingOther ? (
        <output className="banner-info">
          You are viewing {owner.ownerName}&apos;s records. Anything you change is recorded under
          your name.
        </output>
      ) : null}
    </section>
  );
}

/** An empty list: the first-run upload screen for one's own records, a plain line for someone
 *  else's (whose records cannot be uploaded to from here). */
function NoRecords({
  owner,
  dragging,
  uploading,
  onBrowse,
}: Readonly<{ owner: RecordsOwner; dragging: boolean; uploading: boolean; onBrowse: () => void }>) {
  if (owner.viewingOther) {
    return (
      <section className="hd-column">
        <p>{owner.ownerName} has no records.</p>
      </section>
    );
  }
  return <EmptyState dragging={dragging} uploading={uploading} onBrowse={onBrowse} />;
}

/** The list heading, with the upload buttons on one's own records only. */
function ListHeader({
  owner,
  uploading,
  onSplit,
  onPick,
}: Readonly<{ owner: RecordsOwner; uploading: boolean; onSplit: () => void; onPick: () => void }>) {
  return (
    <div className="hd-header">
      <h1>{owner.viewingOther ? `${owner.ownerName}'s documents` : "My documents"}</h1>
      {owner.viewingOther ? null : (
        <div className="flex flex-wrap gap-2.5">
          <button type="button" className="ev-btn ev-btn-outline ev-btn-lg" onClick={onSplit}>
            Upload split records
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary ev-btn-lg"
            onClick={onPick}
            disabled={uploading}
          >
            {uploading ? "Uploading..." : "Upload a record"}
          </button>
        </div>
      )}
    </div>
  );
}

/** My documents: the documents table + upload (button / browse / drag-drop) + first-run empty
 *  state. Upload does NOT start identification (a mis-clicked file must not spend model quota). */
export function DocumentsView() {
  const router = useRouter();
  const owner = useRecordsOwner();
  const viewingOther = owner.viewingOther;
  const { data: docs = [], isLoading, isError, refetch } = useDocuments(owner.ownerId);
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
    if (!file || viewingOther) return;
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
        id={UPLOAD_INPUT_ID}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => void uploadFile(e.target.files?.[0])}
      />

      <OwnerBar owner={owner} />

      {/* A failed fetch leaves `docs` at its [] default, and the first-run screen below would then
          tell a reviewer with records that they have none. Say what happened instead. */}
      {!isLoading && isError ? (
        <div className="banner" role="alert">
          Could not load your documents.{" "}
          <button type="button" className="ev-btn ev-btn-outline" onClick={() => void refetch()}>
            Try again
          </button>
        </div>
      ) : null}
      {!isLoading && !isError && docs.length === 0 ? (
        <NoRecords
          owner={owner}
          dragging={dragging}
          uploading={upload.isPending}
          onBrowse={pickFile}
        />
      ) : null}
      {!isLoading && docs.length > 0 ? (
        <section className="hd-column">
          <ListHeader
            owner={owner}
            uploading={upload.isPending}
            onSplit={() => setSplitOpen(true)}
            onPick={pickFile}
          />
          <DocumentsTable
            docs={docs}
            onOpen={(id) => router.push(`/records/${id}`)}
            onIdentify={onIdentify}
            onDelete={viewingOther ? undefined : (doc) => setDeleteTarget(doc)}
          />
        </section>
      ) : null}

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
