"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  useDeleteDocument,
  useDocuments,
  useSetCompleted,
  useStartIdentification,
  useTeamQueue,
  useUploadDocument,
} from "@/hooks/use-documents";
import { useAccounts } from "@/hooks/use-admin";
import { useCurrentUser } from "@/hooks/use-current-user";
import { depoPending } from "@/lib/billing-sheet";
import { downloadRawPdf, getBillingSheet } from "@/lib/documents-api";
import { humanizeError, isDuplicateRecord } from "@/lib/errors";
import { otherRecordsLine, patientIdFromFilename } from "@/lib/patient-id";
import type { AdminAccount, DocumentListItem } from "@/lib/types";
import { AssignDialog } from "./assign-dialog";
import { BillingSheetDialog } from "./billing-sheet-dialog";
import { DocumentsTable } from "./documents-table";
import { EmptyState, UPLOAD_INPUT_ID } from "./empty-state";
import { PatientIdDialog } from "./patient-id-dialog";
import { ReceivedFilesDialog } from "./received-files-dialog";
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
  // Another reviewer's records. Deleting is left out while viewing them - deleting is not fixing,
  // and the server refuses it too. Uploading is NOT: it goes into that reviewer's account, after a
  // confirmation that names them (the lead reviewer's request).
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

/** The two upload buttons, on one's own records and - for an admin - on another reviewer's. */
function UploadButtons({
  uploading,
  onCombine,
  onPick,
}: Readonly<{ uploading: boolean; onCombine: () => void; onPick: () => void }>) {
  return (
    <div className="flex flex-wrap gap-2.5">
      <button type="button" className="ev-btn ev-btn-outline ev-btn-lg" onClick={onCombine}>
        Upload received files
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
  );
}

/** An empty list: the first-run upload screen for one's own records; for someone else's, a plain
 *  line and the upload buttons (an admin can upload into that reviewer's account). */
function NoRecords({
  owner,
  dragging,
  uploading,
  onBrowse,
  onCombine,
}: Readonly<{
  owner: RecordsOwner;
  dragging: boolean;
  uploading: boolean;
  onBrowse: () => void;
  onCombine: () => void;
}>) {
  if (owner.viewingOther) {
    return (
      <section className="hd-column">
        <div className="hd-header">
          <p>{owner.ownerName} has no records.</p>
          <UploadButtons uploading={uploading} onCombine={onCombine} onPick={onBrowse} />
        </div>
      </section>
    );
  }
  return <EmptyState dragging={dragging} uploading={uploading} onBrowse={onBrowse} />;
}

/** The list heading, with the upload buttons. */
function ListHeader({
  owner,
  uploading,
  onCombine,
  onPick,
}: Readonly<{
  owner: RecordsOwner;
  uploading: boolean;
  onCombine: () => void;
  onPick: () => void;
}>) {
  return (
    <div className="hd-header">
      <h1>{owner.viewingOther ? `${owner.ownerName}'s documents` : "My documents"}</h1>
      <UploadButtons uploading={uploading} onCombine={onCombine} onPick={onPick} />
    </div>
  );
}

/** The team queue (the office's answer, 2026-11-25): records put together from received files
 *  that nobody has taken yet, whoever uploaded them, closest deadline first. Every reviewer sees
 *  it, so whoever decides what to run next sees what is waiting; starting identification on one
 *  takes it into that reviewer's own records. Shown only while something waits. */
function TeamQueue({
  docs,
  ...actions
}: Readonly<
  { docs: DocumentListItem[] } & Omit<Parameters<typeof DocumentsTable>[0], "docs" | "queue">
>) {
  if (docs.length === 0) return null;
  return (
    <section className="hd-column hd-queue" aria-label="Team queue">
      <div className="hd-header">
        <div>
          <h2>Team queue</h2>
          <p className="hd-muted">
            Records nobody has taken yet. Starting identification on one moves it to your records.
          </p>
        </div>
      </div>
      <DocumentsTable queue docs={docs} {...actions} />
    </section>
  );
}

/** A file waiting on the admin's go-ahead to land in another reviewer's account. The reviewer is
 *  captured WITH the file, so what the confirmation names is what the upload targets. */
type CrossUpload = { file: File; ownerId: number; ownerName: string; patientId: string };

/** The sentence an upload's message adds when the patient already has other records, or "". */
function otherRecordsNote(count: number | undefined): string {
  const line = otherRecordsLine(count);
  return line ? ` ${line}` : "";
}

/** Uploading: straight in on one's own records; into another reviewer's after a confirmation, and
 *  a second one if the server says that reviewer already holds the file. A file whose name carries
 *  no patient ID waits for the uploader to type one first (`asking`). */
function useUploads(owner: RecordsOwner) {
  const upload = useUploadDocument();
  const [asking, setAsking] = useState<File | null>(null);
  const [pending, setPending] = useState<CrossUpload | null>(null);
  const [duplicate, setDuplicate] = useState<(CrossUpload & { message: string }) | null>(null);

  async function uploadOwn(file: File, patientId: string) {
    try {
      const created = await upload.mutateAsync({ file, patientId });
      const others = otherRecordsNote(created.patient_records);
      if (created.sha256_duplicate) {
        toast(`You already uploaded an identical file. Continuing anyway.${others}`);
      } else {
        toast.success(`Record uploaded.${others}`);
      }
    } catch (err) {
      toast.error(errMessage(err, "Upload failed."));
    }
  }

  async function uploadFor(to: CrossUpload, allowDuplicate: boolean) {
    try {
      const created = await upload.mutateAsync({
        file: to.file,
        owner: to.ownerId,
        allowDuplicate,
        patientId: to.patientId,
      });
      toast.success(
        `Record uploaded to ${to.ownerName}'s records.${otherRecordsNote(created.patient_records)}`,
      );
    } catch (err) {
      if (isDuplicateRecord(err)) {
        setDuplicate({ ...to, message: err.message });
        return;
      }
      toast.error(
        humanizeError(err, {
          fallback: "Upload failed.",
          notFound: `${to.ownerName}'s account is no longer active. Refresh the page.`,
        }),
      );
    }
  }

  /** A file with its patient ID: into one's own records, or to the admin's confirmation. */
  function send(file: File, patientId: string) {
    if (owner.viewingOther && owner.ownerId !== null) {
      setPending({
        file,
        ownerId: owner.ownerId,
        ownerName: owner.ownerName ?? "this reviewer",
        patientId,
      });
      return;
    }
    void uploadOwn(file, patientId);
  }

  /** A picked or dropped file. Its patient ID comes from the file name; when the name has none (or
   *  two), the uploader is asked for it before anything is sent. */
  function take(file: File) {
    if (!isPdf(file)) {
      toast.error("Only PDF files can be uploaded.");
      return;
    }
    const patientId = patientIdFromFilename(file.name);
    if (patientId) send(file, patientId);
    else setAsking(file);
  }

  /** The uploader typed the ID the file name lacked. */
  function answer(patientId: string) {
    const file = asking;
    setAsking(null);
    if (file) send(file, patientId);
  }

  return {
    upload,
    asking,
    setAsking,
    answer,
    pending,
    setPending,
    duplicate,
    setDuplicate,
    take,
    uploadFor,
  };
}

type Uploads = ReturnType<typeof useUploads>;

/** The questions an upload can ask: the patient ID a file name lacks, and the confirmations an
 *  upload into another reviewer's account goes through. */
function UploadDialogs({ uploads }: Readonly<{ uploads: Uploads }>) {
  const { asking, setAsking, answer, pending, setPending, duplicate, setDuplicate, uploadFor } =
    uploads;
  return (
    <>
      <PatientIdDialog
        fileName={asking?.name ?? null}
        onCancel={() => setAsking(null)}
        onSubmit={answer}
      />
      <ConfirmDialog
        open={Boolean(pending)}
        onOpenChange={(open) => !open && setPending(null)}
        title={`Upload to ${pending?.ownerName ?? ""}'s records?`}
        description={`"${pending?.file.name ?? ""}" will be added to ${pending?.ownerName ?? ""}'s records, not yours. If they already have this record, you will be asked again.`}
        confirmLabel="Upload"
        onConfirm={() => {
          if (pending) void uploadFor(pending, false);
          setPending(null);
        }}
      />
      <ConfirmDialog
        open={Boolean(duplicate)}
        onOpenChange={(open) => !open && setDuplicate(null)}
        title={`${duplicate?.ownerName ?? ""} already has this record`}
        description={`${duplicate?.message ?? ""} Upload another copy anyway?`}
        confirmLabel="Upload anyway"
        onConfirm={() => {
          if (duplicate) void uploadFor(duplicate, true);
          setDuplicate(null);
        }}
      />
    </>
  );
}

/** My documents: the documents table + upload (button / browse / drag-drop) + first-run empty
 *  state. Upload does NOT start identification (a mis-clicked file must not spend model quota).
 *  An admin viewing another reviewer's records uploads into THAT account, after confirming, and
 *  can assign any record shown to another reviewer. */
export function DocumentsView() {
  const router = useRouter();
  const owner = useRecordsOwner();
  const viewingOther = owner.viewingOther;
  const { data: docs = [], isLoading, isError, refetch } = useDocuments(owner.ownerId);
  const { data: queueDocs = [] } = useTeamQueue();
  const uploads = useUploads(owner);
  const del = useDeleteDocument();
  const identify = useStartIdentification();
  const complete = useSetCompleted();

  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [combineOpen, setCombineOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const [reidentifyTarget, setReidentifyTarget] = useState<DocumentListItem | null>(null);
  const [assignTarget, setAssignTarget] = useState<DocumentListItem | null>(null);
  const [billingTarget, setBillingTarget] = useState<DocumentListItem | null>(null);

  function pickFile() {
    fileInput.current?.click();
  }

  function onFile(file: File | undefined) {
    // Cleared at once so picking the same file again still fires `change`; the File stays valid.
    if (fileInput.current) fileInput.current.value = "";
    if (file) uploads.take(file);
  }

  async function runIdentify(id: string, fromQueue = false) {
    try {
      await identify.mutateAsync(id);
      toast.success(
        fromQueue
          ? "Identification started. The record is now in your records."
          : "Identification started.",
      );
    } catch (err) {
      // A record another reviewer took from the queue a moment ago is closed to the caller (404):
      // say that, not that it may have been deleted.
      const taken = "Another reviewer has already taken this record. Refresh the list.";
      toast.error(
        fromQueue
          ? humanizeError(err, { fallback: "Could not start identification.", notFound: taken })
          : errMessage(err, "Could not start identification."),
      );
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

  /** "Download raw PDF", at once. A record put together from received files starts with its
   *  billing sheet as it stands; when its deposition pages are not known yet (the record's pages
   *  still being read), say that box prints blank. */
  async function downloadRaw(doc: DocumentListItem) {
    downloadRawPdf(doc.id);
    if (!doc.received_on) return;
    toast("Putting the billing sheet on top of the raw PDF.");
    let pending: boolean;
    try {
      pending = depoPending(await getBillingSheet(doc.id));
    } catch {
      return; // the note only; the download goes on regardless
    }
    if (pending) {
      toast(
        "The deposition pages on its billing sheet are blank: the record's pages are still being " +
          "read. Type them on the billing sheet, or download again later.",
      );
    }
  }

  function toggleCompleted(doc: DocumentListItem) {
    complete.mutate(
      { id: doc.id, completed: !doc.completed_at },
      { onError: (err) => toast.error(errMessage(err, "Could not change the Completed mark.")) },
    );
  }

  function onIdentify(doc: DocumentListItem) {
    if (doc.in_team_queue) {
      void runIdentify(doc.id, true); // takes the record; it has no rows to lose
    } else if (doc.rows_count) {
      setReidentifyTarget(doc); // re-run replaces corrections -> confirm first
    } else {
      void runIdentify(doc.id);
    }
  }

  // While the received-files upload or a billing sheet is open, a file dropped on the page around
  // it is not an upload of its own: the received-files dialog takes the files dropped on it, and
  // anything dropped beside a dialog is ignored rather than sent as a record.
  const dialogOpen = combineOpen || billingTarget !== null;

  const combineOwner =
    viewingOther && owner.ownerId !== null
      ? { id: owner.ownerId, name: owner.ownerName ?? "this reviewer" }
      : null;

  return (
    <main
      className="flex flex-1 flex-col"
      onDragOver={(e) => {
        e.preventDefault();
        if (!dialogOpen) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (!dialogOpen) onFile(e.dataTransfer.files[0]);
      }}
    >
      <input
        ref={fileInput}
        id={UPLOAD_INPUT_ID}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0])}
      />

      <OwnerBar owner={owner} />

      <TeamQueue
        docs={queueDocs}
        onOpen={(id) => router.push(`/records/${id}`)}
        onIdentify={onIdentify}
        onAssign={owner.isAdmin ? setAssignTarget : undefined}
        onDelete={(doc) => setDeleteTarget(doc)}
        onBillingSheet={setBillingTarget}
        onDownloadRaw={downloadRaw}
      />

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
          uploading={uploads.upload.isPending}
          onBrowse={pickFile}
          onCombine={() => setCombineOpen(true)}
        />
      ) : null}
      {!isLoading && docs.length > 0 ? (
        <section className="hd-column">
          <ListHeader
            owner={owner}
            uploading={uploads.upload.isPending}
            onCombine={() => setCombineOpen(true)}
            onPick={pickFile}
          />
          <DocumentsTable
            docs={docs}
            onOpen={(id) => router.push(`/records/${id}`)}
            onIdentify={onIdentify}
            onAssign={owner.isAdmin ? setAssignTarget : undefined}
            onDelete={viewingOther ? undefined : (doc) => setDeleteTarget(doc)}
            onToggleCompleted={toggleCompleted}
            onBillingSheet={setBillingTarget}
            onDownloadRaw={downloadRaw}
          />
        </section>
      ) : null}

      <ReceivedFilesDialog open={combineOpen} onOpenChange={setCombineOpen} owner={combineOwner} />

      <UploadDialogs uploads={uploads} />

      <BillingSheetDialog doc={billingTarget} onClose={() => setBillingTarget(null)} />

      <AssignDialog
        doc={assignTarget}
        accounts={owner.accounts}
        currentOwnerId={owner.ownerId ?? owner.meId}
        onClose={() => setAssignTarget(null)}
      />

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
