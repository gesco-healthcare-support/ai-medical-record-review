"use client";

import { type DragEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Check, FileText, Upload, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BillingFields } from "@/components/documents/billing-fields";
import { DIALOG_BODY, DIALOG_FRAME } from "@/components/documents/dialog-layout";
import { useCombineReceivedFiles, useDoctors } from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";
import { type BillingDraft, billingReady, EMPTY_BILLING } from "@/lib/billing-sheet";
import {
  receivedFilePreviewUrl,
  stageReceivedFile,
  unstageReceivedFile,
} from "@/lib/documents-api";
import { humanizeError } from "@/lib/errors";
import {
  agreedPatientId,
  cleanPatientId,
  otherRecordsLine,
  PATIENT_ID_MAX_LENGTH,
} from "@/lib/patient-id";
import {
  ACCEPTED_FILES,
  guessKind,
  inReceivedOrder,
  isAcceptedFile,
  RECEIVED_KIND_LABELS,
  RECEIVED_KINDS,
  RECORDS_FROM,
  RECORDS_FROM_LABELS,
  type ReceivedKind,
  type RecordsFrom,
  shownKind,
  type UploadedBefore,
  uploadedBeforeLine,
} from "@/lib/received-files";
import { cn } from "@/lib/utils";

/** One file in the list. It is sent to the server as soon as it is added ("reading"), which keeps
 *  it, counts its pages and reads its first page to say what it is ("ready"), or refuses it
 *  ("failed"). When there are several files, every one must be CONFIRMED by the uploader - "Looks
 *  right", or a kind picked - before the record is made: the reading pre-fills the kind, a person
 *  checks it against the file's first page (measured 2026-11-19: the model alone recognised the
 *  case's own papers about 3 times in 4). Once confirmed, the server's reading never overrides the
 *  kind. ONE file is the record as it came: no kind is asked, and it is sent as the medical
 *  records. `check` marks a reading the system is not sure of (the model unsure or off, or the name
 *  saying otherwise). `sha256` is the file's fingerprint, which names a file added twice;
 *  `uploadedBefore` the records the office already has it in (any account, newest first). */
type Item = {
  key: number;
  file: File;
  kind: ReceivedKind;
  confirmed: boolean;
  check: boolean;
  state: "reading" | "ready" | "failed";
  token?: string;
  pages?: number;
  sha256?: string;
  uploadedBefore: UploadedBefore[];
  error?: string;
};

function toConfirmLine(count: number): string {
  const files = count === 1 ? "1 file" : `${count} files`;
  return `${files} to confirm before the record can be made.`;
}

/** For each file that is the same file as one listed above it, that earlier file's name. Joined
 *  twice, its pages would be reviewed twice. */
function sameFileAs(items: readonly Item[]): Map<number, string> {
  const first = new Map<string, string>();
  const repeats = new Map<number, string>();
  for (const item of items) {
    if (!item.sha256) continue;
    const earlier = first.get(item.sha256);
    if (earlier === undefined) first.set(item.sha256, item.file.name);
    else repeats.set(item.key, earlier);
  }
  return repeats;
}

function stagingError(err: unknown): string {
  if (err instanceof ApiError && err.status === 413) {
    return "This file is larger than the server accepts in one upload.";
  }
  return humanizeError(err, { fallback: "Could not read this file." });
}

function forget(token: string | undefined) {
  if (token) unstageReceivedFile(token).catch(() => undefined);
}

/** Files sent to the server at once. One at a time, each file also waiting for its first page to
 *  be read, ten files added up to minutes before the record could be made. */
const STAGE_AT_ONCE = 3;

/** The list of received files and their staging on the server. Files are sent `STAGE_AT_ONCE` at
 *  a time, in the order added; a file removed before its turn is never sent, and one removed (or
 *  the dialog closed) before its answer arrives is dropped on the server when the answer comes. */
function useReceivedFiles() {
  const [items, setItems] = useState<Item[]>([]);
  const nextKey = useRef(0);
  const live = useRef(new Set<number>());
  // Each file's token on the server, by key, so removing and clearing drop it there too.
  const tokens = useRef(new Map<number, string>());
  const waiting = useRef<Item[]>([]);
  const sending = useRef(0);

  /** Start the next waiting files while fewer than `STAGE_AT_ONCE` are being sent. */
  function pump() {
    while (sending.current < STAGE_AT_ONCE) {
      const item = waiting.current.shift();
      if (item === undefined) return;
      if (!live.current.has(item.key)) continue;
      sending.current += 1;
      void stage(item).finally(() => {
        sending.current -= 1;
        pump();
      });
    }
  }

  function send(item: Item) {
    waiting.current.push(item);
    pump();
  }

  function update(key: number, change: (item: Item) => Item) {
    setItems((prev) => {
      const next = prev.map((item) => (item.key === key ? change(item) : item));
      const before = prev.find((item) => item.key === key);
      const after = next.find((item) => item.key === key);
      return before && after && before.kind !== after.kind ? inReceivedOrder(next) : next;
    });
  }

  async function stage(item: Item) {
    try {
      const staged = await stageReceivedFile(item.file);
      if (!live.current.has(item.key)) {
        forget(staged.token);
        return;
      }
      tokens.current.set(item.key, staged.token);
      update(item.key, (current) => {
        const shown = current.confirmed
          ? { kind: current.kind, check: false }
          : shownKind(staged, guessKind(current.file.name));
        return {
          ...current,
          state: "ready",
          token: staged.token,
          pages: staged.pages,
          sha256: staged.sha256,
          uploadedBefore: staged.uploaded_before ?? [],
          kind: shown.kind,
          check: shown.check,
        };
      });
    } catch (err) {
      if (live.current.has(item.key)) {
        update(item.key, (current) => ({ ...current, state: "failed", error: stagingError(err) }));
      }
    }
  }

  function add(files: File[]) {
    const added: Item[] = files.map((file) => ({
      key: nextKey.current++,
      file,
      kind: guessKind(file.name),
      confirmed: false,
      check: false,
      state: "reading",
      uploadedBefore: [],
    }));
    for (const item of added) live.current.add(item.key);
    setItems((prev) => inReceivedOrder([...prev, ...added]));
    for (const item of added) send(item);
  }

  function setKind(key: number, kind: ReceivedKind) {
    update(key, (item) => ({ ...item, kind, confirmed: true, check: false }));
  }

  /** The uploader looked and the kind shown is right. */
  function confirm(key: number) {
    update(key, (item) => ({ ...item, confirmed: true, check: false }));
  }

  /** Send a refused file again (a dropped connection, a session that had expired). */
  function retry(key: number) {
    const item = items.find((candidate) => candidate.key === key);
    if (item?.state !== "failed") return;
    update(key, (current) => ({ ...current, state: "reading", error: undefined }));
    send(item);
  }

  function move(index: number, step: -1 | 1) {
    setItems((prev) => {
      const next = [...prev];
      [next[index], next[index + step]] = [next[index + step], next[index]];
      return next;
    });
  }

  function remove(key: number) {
    live.current.delete(key);
    forget(tokens.current.get(key));
    tokens.current.delete(key);
    setItems((prev) => prev.filter((item) => item.key !== key));
  }

  /** Empty the list. `keepOnServer` after the record was made: the server used the files and has
   *  already deleted them. */
  function clear(keepOnServer = false) {
    live.current.clear();
    waiting.current = [];
    if (!keepOnServer) tokens.current.forEach((token) => forget(token));
    tokens.current.clear();
    setItems([]);
  }

  return { items, add, setKind, confirm, retry, move, remove, clear };
}

type Files = ReturnType<typeof useReceivedFiles>;

/** Under a file's name: reading, its pages, why it was refused (with Try again), that it is the
 *  same file as another, or - until the uploader confirms its kind with Looks right - a call to
 *  confirm it, marked when the system is not sure. A file alone (`single`) is the record as it
 *  came: its pages only, nothing to confirm. */
function FileStatus({
  item,
  sameAs,
  single,
  files,
}: Readonly<{ item: Item; sameAs: string | undefined; single: boolean; files: Files }>) {
  const name = item.file.name;
  if (item.state === "reading") {
    return <span className="text-muted-foreground">Reading...</span>;
  }
  if (item.state === "failed") {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-danger">{item.error}</span>
        <button
          type="button"
          className="ev-btn ev-btn-ghost ev-btn-sm"
          aria-label={`Try ${name} again`}
          onClick={() => files.retry(item.key)}
        >
          Try again
        </button>
      </span>
    );
  }
  if (sameAs !== undefined) {
    return <span className="text-danger">Same file as {sameAs}. Remove one of them.</span>;
  }
  const pages = item.pages === 1 ? "1 page" : `${item.pages} pages`;
  if (single) return <span className="text-muted-foreground">{pages}</span>;
  if (item.confirmed) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">{pages}</span>
        <span className="ev-chip ev-chip-ok">
          <Check className="size-3" aria-hidden />
          Confirmed
        </span>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-muted-foreground">{pages}</span>
      {item.check ? (
        <span className="ev-chip ev-chip-review">Not sure - check the page</span>
      ) : null}
      <button
        type="button"
        className="ev-btn ev-btn-outline ev-btn-sm"
        aria-label={`Confirm ${name} as ${RECEIVED_KIND_LABELS[item.kind]}`}
        onClick={() => files.confirm(item.key)}
      >
        <Check className="size-3.5" aria-hidden />
        Looks right
      </button>
    </span>
  );
}

const PLAIN = "border-gray-200 bg-white";
const AMBER = "border-warning/60 bg-warning-soft/40";

/** A file's card: red while it cannot be used as it is, amber when the office already has the
 *  file, green once confirmed, amber while the system is not sure what it is, plain otherwise - and
 *  plain for a file alone, which has nothing to confirm. */
function rowTone(item: Item, sameAs: string | undefined, single: boolean): string {
  if (item.state === "failed" || sameAs !== undefined) return "border-danger/40 bg-danger-soft/40";
  if (item.state !== "ready") return PLAIN;
  if (item.uploadedBefore.length > 0) return AMBER;
  if (single) return PLAIN;
  if (item.confirmed) return "border-success/40 bg-success-soft/50";
  return item.check ? AMBER : PLAIN;
}

const ICON_BUTTON =
  "grid size-7 place-items-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900 " +
  "disabled:opacity-30 disabled:hover:bg-transparent";

const SECTION_HEADING = "text-xs font-semibold tracking-wide text-gray-500 uppercase";

function FileRow({
  item,
  index,
  count,
  sameAs,
  files,
}: Readonly<{
  item: Item;
  index: number;
  count: number;
  sameAs: string | undefined;
  files: Files;
}>) {
  const name = item.file.name;
  const [showPage, setShowPage] = useState(false);
  // A file alone is the record as it came: no kind to choose, no place to move it to.
  const single = count === 1;
  const earlier = item.state === "ready" ? uploadedBeforeLine(item.uploadedBefore) : "";
  const tone = rowTone(item, sameAs, single);
  return (
    <li className={cn("grid gap-2 rounded-lg border px-3 py-2.5 text-sm", tone)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 sm:flex-nowrap">
        <span
          className={cn(
            "grid size-6 shrink-0 place-items-center rounded-full bg-navy-50",
            "text-xs font-semibold text-navy-700",
          )}
          aria-hidden
        >
          {index + 1}
        </span>
        <FileText className="size-4 shrink-0 text-gray-400" aria-hidden />
        <span className="grid min-w-0 flex-1 basis-48 gap-1">
          <span className="truncate font-medium text-ink" title={name} data-file-name>
            {name}
          </span>
          <span className="text-xs">
            <FileStatus item={item} sameAs={sameAs} single={single} files={files} />
          </span>
          {earlier ? <span className="text-xs font-medium text-warning">{earlier}</span> : null}
        </span>
        <span className="flex shrink-0 items-center gap-1">
          {single ? null : (
            <select
              className="ev-inp w-44"
              aria-label={`What ${name} is`}
              value={item.kind}
              onChange={(e) => files.setKind(item.key, e.target.value as ReceivedKind)}
            >
              {RECEIVED_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {RECEIVED_KIND_LABELS[kind]}
                </option>
              ))}
            </select>
          )}
          {item.token ? (
            <button
              type="button"
              className="ev-btn ev-btn-ghost ev-btn-sm"
              aria-expanded={showPage}
              aria-label={`${showPage ? "Hide" : "Show"} the first page of ${name}`}
              onClick={() => setShowPage((shown) => !shown)}
            >
              Page 1
            </button>
          ) : null}
          {single ? null : (
            <>
              <button
                type="button"
                aria-label={`Move ${name} up`}
                className={ICON_BUTTON}
                disabled={index === 0}
                onClick={() => files.move(index, -1)}
              >
                <ArrowUp className="size-4" aria-hidden />
              </button>
              <button
                type="button"
                aria-label={`Move ${name} down`}
                className={ICON_BUTTON}
                disabled={index === count - 1}
                onClick={() => files.move(index, 1)}
              >
                <ArrowDown className="size-4" aria-hidden />
              </button>
            </>
          )}
          <button
            type="button"
            aria-label={`Remove ${name}`}
            className={cn(ICON_BUTTON, "hover:bg-danger-soft hover:text-danger")}
            onClick={() => files.remove(item.key)}
          >
            <X className="size-4" aria-hidden />
          </button>
        </span>
      </div>
      {showPage && item.token ? (
        // eslint-disable-next-line @next/next/no-img-element -- a same-origin PNG, by cookie
        <img
          src={receivedFilePreviewUrl(item.token)}
          alt={`First page of ${name}`}
          className="max-h-[32rem] w-full rounded border border-gray-200 object-contain"
        />
      ) : null}
    </li>
  );
}

/** Files dragged onto the dialog, anywhere on it, go to `onFiles` like picked ones (none while
 *  `paused`). The events stop at the dialog: React passes a dialog's events up to the page it was
 *  opened from, and the records page's own drop handler would otherwise upload the first file alone
 *  as a record of its own - which is what a drop on this dialog did until the office's first use
 *  (2026-11-20) found it. Dragging from one of the dialog's parts to another enters the new part
 *  before it leaves the old one, so the parts entered are counted and `dragging` stays on until the
 *  last is left. */
function useFileDrop(onFiles: (list: FileList) => void, paused: boolean) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  function reset() {
    depth.current = 0;
    setDragging(false);
  }

  const handlers = {
    onDragEnter: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(true);
    },
    onDragLeave: (e: DragEvent) => {
      e.stopPropagation();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      reset();
      if (!paused) onFiles(e.dataTransfer.files);
    },
  };

  return { dragging, reset, handlers };
}

/** Where files are added: dragged onto the dialog (anywhere on it, `useFileDrop`), or picked with
 *  Add files. It lights up while files are dragged over the dialog, under `DropCover`. */
function DropZone({
  dragging,
  empty,
  onPick,
}: Readonly<{ dragging: boolean; empty: boolean; onPick: () => void }>) {
  const line = empty ? "Drag the file or files here, in any order, or" : "Drag more files here, or";
  return (
    <div
      data-dropzone
      className={cn(
        "flex flex-wrap items-center justify-center gap-x-3 gap-y-2 rounded-lg border-[1.5px]",
        "border-dashed px-4 text-center text-sm transition-colors",
        empty ? "py-6" : "py-3",
        dragging ? "border-blue-500 bg-blue-100/60" : "border-gray-300 bg-gray-50/60",
      )}
    >
      <Upload className="size-4 text-navy-600" aria-hidden />
      <span className="text-gray-600">{line}</span>
      <button type="button" className="ev-btn ev-btn-outline ev-btn-sm" onClick={onPick}>
        Add files
      </button>
      {empty ? (
        <span className="basis-full text-xs text-muted-foreground">
          PDFs and scanned images (JPG, PNG, TIFF)
        </span>
      ) : null}
    </div>
  );
}

/** Under the list once every file is read: the pages in all, and how many files are left to
 *  confirm - in amber while any are. */
function FilesTotal({ items, toCheck }: Readonly<{ items: readonly Item[]; toCheck: number }>) {
  const pages = items.reduce((sum, item) => sum + (item.pages ?? 0), 0);
  const left = toCheck > 0;
  return (
    <span className={cn("text-sm", left ? "font-medium text-warning" : "text-muted-foreground")}>
      {pages === 1 ? "1 page in all" : `${pages} pages in all`}
      {left ? `. ${toConfirmLine(toCheck)}` : null}
    </span>
  );
}

/** Above the buttons once the uploader asked to upload files the office already has: which files,
 *  where each one is, and whether to upload anyway. Uploading again is the uploader's choice -
 *  re-running a case is legitimate - so nothing is refused. */
function UploadedBeforeQuestion({ items }: Readonly<{ items: readonly Item[] }>) {
  const lead =
    items.length === 1
      ? "This file was uploaded before."
      : `These ${items.length} files were uploaded before.`;
  return (
    <div role="alert" className={cn("grid shrink-0 gap-1.5 rounded-lg border px-3 py-2.5", AMBER)}>
      <p className="text-sm font-medium text-ink">{lead} Upload anyway?</p>
      <ul className="grid gap-1 text-xs text-gray-700">
        {items.map((item) => (
          <li key={item.key}>
            <span className="font-medium">{item.file.name}</span>:{" "}
            {uploadedBeforeLine(item.uploadedBefore)}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Over the whole dialog while files are dragged across it: the drop lands wherever it is let go,
 *  and the list may be scrolled out of sight. It lets the drag events through to the dialog. */
function DropCover() {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-xl",
        "border-2 border-dashed border-blue-500 bg-blue-100/95",
      )}
    >
      <span className="flex items-center gap-2 text-base font-medium text-blue-600">
        <Upload className="size-5" />
        Drop the files to add them
      </span>
    </div>
  );
}

/** "Upload records", the web app's one upload (the office's answer, 2026-11-26): a record that
 *  came as one file, or one put together from the files it arrived in - the step the office used
 *  to do in Adobe Acrobat. Either way the uploader gives the patient's name, ID, the day the
 *  records were received, who sent them and the billing sheet's fields, so every record can be
 *  listed by its deadline and has its billing sheet.
 *
 *  Each file is sent as soon as it is added (POST /api/documents/combine/files): the server counts
 *  its pages, reads its first page to tell what it is, and names any record that already holds the
 *  same file, which the file's card and - before anything is sent - a question repeat. ONE file is
 *  uploaded as it is, as the medical records. SEVERAL are kept in the office's order
 *  (`lib/received-files.ts`) whatever order they were added in; a kind can still be changed and a
 *  file moved, every kind is confirmed, and Combine & upload joins the files exactly as listed
 *  (POST /api/documents/combine). Nothing starts Identify.
 *
 *  `owner` is set when an admin opens it on another reviewer's records: the record goes into THAT
 *  account, and the dialog says so in its title, its description and its button. `dropped` are
 *  files dropped on the records page that opened it: added once, as if picked. */
export function ReceivedFilesDialog({
  open,
  onOpenChange,
  owner = null,
  dropped = null,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  owner?: { id: number; name: string } | null;
  dropped?: File[] | null;
}>) {
  const combine = useCombineReceivedFiles();
  const doctors = useDoctors(open).data ?? [];
  const files = useReceivedFiles();
  const { items } = files;
  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [dateReceived, setDateReceived] = useState("");
  const [recordsFrom, setRecordsFrom] = useState<RecordsFrom | "">("");
  const [billing, setBilling] = useState<BillingDraft>(EMPTY_BILLING);
  // What the uploader typed in the Patient ID box, or null while they have not touched it - the box
  // then shows the ID the files' names agree on.
  const [typedId, setTypedId] = useState<string | null>(null);
  // The files the office already has, as they stood when the uploader was asked about them; the
  // question goes away as soon as that set changes (one removed, another added).
  const [askedAbout, setAskedAbout] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const drop = useFileDrop((list) => addFiles(list), combine.isPending);

  // Files dropped on the records page: added once. The same list is never added twice - a render
  // in React's strict mode runs this effect twice - and a new drop is a new list.
  const takenDrop = useRef<File[] | null>(null);
  const addDropped = useRef(addFiles);
  addDropped.current = addFiles;
  useEffect(() => {
    if (!open || !dropped || takenDrop.current === dropped) return;
    takenDrop.current = dropped;
    addDropped.current(dropped);
  }, [open, dropped]);

  const foundId = agreedPatientId(items.map((item) => item.file.name)) ?? "";
  const patientId = cleanPatientId(typedId ?? foundId);
  const idTooLong = patientId.length > PATIENT_ID_MAX_LENGTH;
  const allRead = items.length > 0 && items.every((item) => item.state === "ready");
  const single = items.length === 1;
  const sameAs = sameFileAs(items);
  // Files the uploader must still look at before the record can be made: with several files every
  // file's kind is confirmed by a person, and a repeated file is removed. A file alone has no kind
  // to confirm.
  const unconfirmed = single
    ? 0
    : items.filter((item) => item.state === "ready" && !item.confirmed).length;
  const toCheck = unconfirmed + sameAs.size;
  const seenBefore = items.filter(
    (item) => item.state === "ready" && item.uploadedBefore.length > 0,
  );
  const seenKey = seenBefore.map((item) => item.key).join(",");
  const asking = seenBefore.length > 0 && askedAbout === seenKey;
  const ready =
    allRead &&
    toCheck === 0 &&
    lastName.trim() !== "" &&
    firstName.trim() !== "" &&
    patientId !== "" &&
    !idTooLong &&
    dateReceived !== "" &&
    recordsFrom !== "" &&
    billingReady(billing);

  /** Dismissing clears everything, whichever control did it (one function, so Cancel and Escape
   *  cannot drift apart again - #264's shape). The files already sent are dropped on the server,
   *  unless the record was made from them. */
  function close(recordMade = false) {
    setLastName("");
    setFirstName("");
    setDateReceived("");
    setRecordsFrom("");
    setBilling(EMPTY_BILLING);
    files.clear(recordMade);
    setTypedId(null);
    setAskedAbout(null);
    drop.reset();
    onOpenChange(false);
  }

  function addFiles(list: FileList | readonly File[] | null) {
    if (!list) return;
    // Say what was dropped: these files become ONE record, so an unnoticed drop is content missing.
    const picked = Array.from(list);
    const accepted = picked.filter(isAcceptedFile);
    const rejected = picked.length - accepted.length;
    if (rejected > 0) {
      const what =
        "Only PDFs and scanned images (JPG, PNG, TIFF) can be added; save Word or Outlook files " +
        "as PDF first.";
      toast.error(
        rejected === 1
          ? `${what} One file was not added.`
          : `${what} ${rejected} files were not added.`,
      );
    }
    files.add(accepted);
    if (fileInput.current) fileInput.current.value = "";
  }

  /** Upload - but first ask, once, about any file the office already has. */
  function send() {
    if (seenBefore.length > 0 && !asking) setAskedAbout(seenKey);
    else void submit();
  }

  async function submit() {
    try {
      const created = await combine.mutateAsync({
        lastName,
        firstName,
        patientId,
        dateReceived,
        // Send is enabled only when `ready`, so a party has been chosen and the billing sheet's
        // required fields are filled.
        recordsFrom: recordsFrom as RecordsFrom,
        billing,
        // ...and every file has been read, so every file has its token. A file alone is the
        // record as it came: its pages are the medical records, whatever its first page reads as.
        files: items.map(({ token, kind }) => ({
          token: token as string,
          kind: single ? "records" : kind,
        })),
        owner: owner?.id ?? null,
      });
      const others = otherRecordsLine(created.patient_records);
      const pages = created.page_count === 1 ? "1 page" : `${created.page_count} pages`;
      const made = single
        ? `Record uploaded, ${pages}`
        : `One record made from ${items.length} files, ${pages}`;
      let where = `${made}.`;
      if (owner) where = `${made}, in ${owner.name}'s records.`;
      else if (created.in_team_queue) where = `${made}, in the team queue.`;
      toast.success(where + (others ? ` ${others}` : ""));
      close(true);
    } catch (err) {
      const fallback = single ? "Could not upload the file." : "Could not combine the files.";
      toast.error(humanizeError(err, { fallback }));
    }
  }

  const action = items.length > 1 ? "Combine & upload" : "Upload";
  let submitLabel = owner ? `${action} for ${owner.name}` : action;
  if (combine.isPending) submitLabel = items.length > 1 ? "Combining..." : "Uploading...";
  if (asking && !combine.isPending) submitLabel = "Upload anyway";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else close();
      }}
    >
      <DialogContent className={cn(DIALOG_FRAME, "sm:max-w-3xl")} {...drop.handlers}>
        <DialogHeader className="shrink-0">
          <DialogTitle>{owner ? `Upload records for ${owner.name}` : "Upload records"}</DialogTitle>
          <DialogDescription>
            Add the record&apos;s file, or every file it came in, in any order. One file is
            uploaded as it is. Several files are put together in their place: advocacy letter,
            declaration, medical index, medical records, proof of service, internal email - look
            at each file&apos;s first page and confirm its kind, or change it.
            {owner ? ` The record goes into ${owner.name}'s records, not yours.` : null}
          </DialogDescription>
        </DialogHeader>

        <div className={DIALOG_BODY}>
          {/* The files first: a drop shows them at once, and they are read while the rest is
              typed - the patient ID box fills from their names. */}
          <section className="grid gap-2" aria-labelledby="receivedFilesHeading">
            <h3 id="receivedFilesHeading" className={SECTION_HEADING}>
              {items.length > 1 ? "Files, in the order they are put together" : "Files"}
            </h3>
            {items.length === 0 ? null : (
              <ol className="grid gap-2">
                {items.map((item, index) => (
                  <FileRow
                    key={item.key}
                    item={item}
                    index={index}
                    count={items.length}
                    sameAs={sameAs.get(item.key)}
                    files={files}
                  />
                ))}
              </ol>
            )}
            {allRead && !single ? <FilesTotal items={items} toCheck={toCheck} /> : null}
            <DropZone
              dragging={drop.dragging}
              empty={items.length === 0}
              onPick={() => fileInput.current?.click()}
            />
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPTED_FILES}
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </section>

          <h3 className={cn(SECTION_HEADING, "-mb-1 border-t border-gray-100 pt-4")}>
            The patient and the records
          </h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <label className="ev-lbl" htmlFor="receivedLastName">
                Patient last name
              </label>
              <input
                id="receivedLastName"
                className="ev-inp"
                autoComplete="off"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="ev-lbl" htmlFor="receivedFirstName">
                Patient first name
              </label>
              <input
                id="receivedFirstName"
                className="ev-inp"
                autoComplete="off"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="ev-lbl" htmlFor="receivedPatientId">
                Patient ID
              </label>
              <input
                id="receivedPatientId"
                className="ev-inp"
                autoComplete="off"
                placeholder="Read from the file names, or type it"
                value={typedId ?? foundId}
                onChange={(e) => setTypedId(e.target.value)}
              />
              {idTooLong ? (
                <output className="rc-hb-warn">
                  A patient ID is at most {PATIENT_ID_MAX_LENGTH} letters and digits.
                </output>
              ) : null}
            </div>
            <div className="grid gap-1.5">
              <label className="ev-lbl" htmlFor="receivedDate">
                Date received
              </label>
              <input
                id="receivedDate"
                type="date"
                className="ev-inp"
                value={dateReceived}
                onChange={(e) => setDateReceived(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="ev-lbl" htmlFor="receivedFrom">
                Records from
              </label>
              <select
                id="receivedFrom"
                className="ev-inp"
                value={recordsFrom}
                onChange={(e) => setRecordsFrom(e.target.value as RecordsFrom | "")}
              >
                <option value="">Choose...</option>
                {RECORDS_FROM.map((party) => (
                  <option key={party} value={party}>
                    {RECORDS_FROM_LABELS[party]}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="border-t border-gray-100 pt-4">
            <fieldset className="grid gap-3 sm:grid-cols-2">
              <legend className={cn(SECTION_HEADING, "mb-3")}>For the billing sheet</legend>
              <BillingFields
                value={billing}
                onChange={(patch) => setBilling((prev) => ({ ...prev, ...patch }))}
                doctors={doctors}
                idPrefix="received"
              />
            </fieldset>
          </div>
        </div>

        {asking ? <UploadedBeforeQuestion items={seenBefore} /> : null}
        <DialogFooter className="shrink-0">
          <button
            type="button"
            className="ev-btn ev-btn-outline"
            onClick={() => (asking ? setAskedAbout(null) : close())}
            disabled={combine.isPending}
          >
            {asking ? "Go back" : "Cancel"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={send}
            disabled={combine.isPending || !ready}
          >
            {submitLabel}
          </button>
        </DialogFooter>
        {drop.dragging ? <DropCover /> : null}
      </DialogContent>
    </Dialog>
  );
}
