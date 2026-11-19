"use client";

import { type ReactNode, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCombineReceivedFiles } from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";
import { stageReceivedFile, unstageReceivedFile } from "@/lib/documents-api";
import { humanizeError } from "@/lib/errors";
import {
  agreedPatientId,
  cleanPatientId,
  otherRecordsLine,
  PATIENT_ID_MAX_LENGTH,
} from "@/lib/patient-id";
import {
  guessKind,
  inReceivedOrder,
  pickKind,
  RECEIVED_KIND_LABELS,
  RECEIVED_KINDS,
  RECORDS_FROM,
  RECORDS_FROM_LABELS,
  type ReceivedKind,
  type RecordsFrom,
} from "@/lib/received-files";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** One file in the list. It is sent to the server as soon as it is added ("reading"), which keeps
 *  it, counts its pages and reads its first page to say what it is ("ready"), or refuses it
 *  ("failed"). `kindChosen` is set once the uploader picks a kind, so the server's reading never
 *  overrides that choice. */
type Item = {
  key: number;
  file: File;
  kind: ReceivedKind;
  kindChosen: boolean;
  state: "reading" | "ready" | "failed";
  token?: string;
  pages?: number;
  error?: string;
};

function stagingError(err: unknown): string {
  if (err instanceof ApiError && err.status === 413) {
    return "This file is larger than the server accepts in one upload.";
  }
  return humanizeError(err, { fallback: "Could not read this file." });
}

function forget(token: string | undefined) {
  if (token) unstageReceivedFile(token).catch(() => undefined);
}

/** The list of received files and their staging on the server. Files are sent one at a time, in
 *  the order added; a file removed (or the dialog closed) before its answer arrives is dropped on
 *  the server when the answer comes. */
function useReceivedFiles() {
  const [items, setItems] = useState<Item[]>([]);
  const nextKey = useRef(0);
  const live = useRef(new Set<number>());
  // Each file's token on the server, by key, so removing and clearing drop it there too.
  const tokens = useRef(new Map<number, string>());
  const queue = useRef<Promise<void>>(Promise.resolve());

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
      update(item.key, (current) => ({
        ...current,
        state: "ready",
        token: staged.token,
        pages: staged.pages,
        kind: current.kindChosen
          ? current.kind
          : pickKind(staged.kind, guessKind(current.file.name)),
      }));
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
      kindChosen: false,
      state: "reading",
    }));
    for (const item of added) {
      live.current.add(item.key);
      queue.current = queue.current.then(() => stage(item));
    }
    setItems((prev) => inReceivedOrder([...prev, ...added]));
  }

  function setKind(key: number, kind: ReceivedKind) {
    update(key, (item) => ({ ...item, kind, kindChosen: true }));
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
    if (!keepOnServer) tokens.current.forEach((token) => forget(token));
    tokens.current.clear();
    setItems([]);
  }

  return { items, add, setKind, move, remove, clear };
}

function FileRow({
  item,
  index,
  count,
  files,
}: Readonly<{
  item: Item;
  index: number;
  count: number;
  files: ReturnType<typeof useReceivedFiles>;
}>) {
  const name = item.file.name;
  let status: ReactNode = <span className="text-muted-foreground">Reading...</span>;
  if (item.state === "ready") {
    status = (
      <span className="text-muted-foreground">
        {item.pages === 1 ? "1 page" : `${item.pages} pages`}
      </span>
    );
  } else if (item.state === "failed") {
    status = <span className="text-danger">{item.error}</span>;
  }
  return (
    <li className="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm">
      <span className="w-5 shrink-0 text-right text-muted-foreground">{index + 1}.</span>
      <span className="grid min-w-0 flex-1">
        <span className="truncate" data-file-name>
          {name}
        </span>
        <span className="text-xs">{status}</span>
      </span>
      <select
        className="ev-inp w-auto"
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
      <button
        type="button"
        aria-label={`Move ${name} up`}
        className="text-gray-500 hover:text-gray-900 disabled:opacity-30"
        disabled={index === 0}
        onClick={() => files.move(index, -1)}
      >
        <ArrowUp className="size-4" aria-hidden />
      </button>
      <button
        type="button"
        aria-label={`Move ${name} down`}
        className="text-gray-500 hover:text-gray-900 disabled:opacity-30"
        disabled={index === count - 1}
        onClick={() => files.move(index, 1)}
      >
        <ArrowDown className="size-4" aria-hidden />
      </button>
      <button
        type="button"
        aria-label={`Remove ${name}`}
        className="text-gray-400 hover:text-danger"
        onClick={() => files.remove(item.key)}
      >
        <X className="size-4" aria-hidden />
      </button>
    </li>
  );
}

/** Put one record together from the files it arrived in, the step the office used to do in Adobe
 *  Acrobat. Each file is sent as soon as it is added (POST /api/documents/combine/files): the
 *  server counts its pages and reads its first page to tell what it is, and the list keeps the
 *  files in the office's order (`lib/received-files.ts`) whatever order they were added in. A kind
 *  can still be changed and a file moved. The uploader gives the patient's name, ID, the day the
 *  records were received, the appointment date and who sent them; Combine & upload then joins the
 *  files exactly as listed (POST /api/documents/combine). Nothing starts Identify.
 *
 *  `owner` is set when an admin opens it on another reviewer's records: the record goes into THAT
 *  account, and the dialog says so in its title, its description and its button. */
export function ReceivedFilesDialog({
  open,
  onOpenChange,
  owner = null,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  owner?: { id: number; name: string } | null;
}>) {
  const combine = useCombineReceivedFiles();
  const files = useReceivedFiles();
  const { items } = files;
  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [dateReceived, setDateReceived] = useState("");
  const [appointmentDate, setAppointmentDate] = useState("");
  const [recordsFrom, setRecordsFrom] = useState<RecordsFrom | "">("");
  // What the uploader typed in the Patient ID box, or null while they have not touched it - the box
  // then shows the ID the files' names agree on.
  const [typedId, setTypedId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const foundId = agreedPatientId(items.map((item) => item.file.name)) ?? "";
  const patientId = cleanPatientId(typedId ?? foundId);
  const idTooLong = patientId.length > PATIENT_ID_MAX_LENGTH;
  const allRead = items.length > 0 && items.every((item) => item.state === "ready");
  const totalPages = items.reduce((sum, item) => sum + (item.pages ?? 0), 0);
  const ready =
    allRead &&
    lastName.trim() !== "" &&
    firstName.trim() !== "" &&
    patientId !== "" &&
    !idTooLong &&
    dateReceived !== "" &&
    appointmentDate !== "" &&
    recordsFrom !== "";

  /** Dismissing clears everything, whichever control did it (one function, so Cancel and Escape
   *  cannot drift apart again - #264's shape). The files already sent are dropped on the server,
   *  unless the record was made from them. */
  function close(recordMade = false) {
    setLastName("");
    setFirstName("");
    setDateReceived("");
    setAppointmentDate("");
    setRecordsFrom("");
    files.clear(recordMade);
    setTypedId(null);
    onOpenChange(false);
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    // Say what was dropped: these files become ONE record, so an unnoticed drop is content missing.
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
    files.add(pdfs);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function submit() {
    try {
      const created = await combine.mutateAsync({
        lastName,
        firstName,
        patientId,
        dateReceived,
        appointmentDate,
        // Send is enabled only when `ready`, so a party has been chosen.
        recordsFrom: recordsFrom as RecordsFrom,
        // ...and every file has been read, so every file has its token.
        files: items.map(({ token, kind }) => ({ token: token as string, kind })),
        owner: owner?.id ?? null,
      });
      const others = otherRecordsLine(created.patient_records);
      const count = items.length === 1 ? "1 file" : `${items.length} files`;
      const made = `One record made from ${count}, ${created.page_count} pages`;
      toast.success(
        (owner ? `${made}, in ${owner.name}'s records.` : `${made}.`) +
          (others ? ` ${others}` : ""),
      );
      close(true);
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not combine the files." }));
    }
  }

  let submitLabel = owner ? `Combine & upload for ${owner.name}` : "Combine & upload";
  if (combine.isPending) submitLabel = "Combining...";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else close();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {owner ? `Upload received files for ${owner.name}` : "Upload received files"}
          </DialogTitle>
          <DialogDescription>
            Add the files the records came in, in any order. Each file is read as it is added and
            put in its place: advocacy letter, declaration, medical index, medical records, proof
            of service, internal email. Change a file&apos;s kind or move it if it belongs
            elsewhere.
            {owner ? ` The record goes into ${owner.name}'s records, not yours.` : null}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
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
              <label className="ev-lbl" htmlFor="receivedAppointment">
                Appointment date
              </label>
              <input
                id="receivedAppointment"
                type="date"
                className="ev-inp"
                value={appointmentDate}
                onChange={(e) => setAppointmentDate(e.target.value)}
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

          <div className="grid gap-1.5">
            <span className="ev-lbl">Files, in the order they are put together</span>
            {items.length === 0 ? (
              <div className="rounded-md border-[1.5px] border-dashed border-gray-300 px-3 py-4 text-center text-sm text-muted-foreground">
                No files yet.
              </div>
            ) : (
              <ol className="grid gap-1.5">
                {items.map((item, index) => (
                  <FileRow
                    key={item.key}
                    item={item}
                    index={index}
                    count={items.length}
                    files={files}
                  />
                ))}
              </ol>
            )}
            {allRead ? (
              <span className="text-sm text-muted-foreground">
                {totalPages === 1 ? "1 page in all" : `${totalPages} pages in all`}
              </span>
            ) : null}
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
            onClick={() => close()}
            disabled={combine.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={submit}
            disabled={combine.isPending || !ready}
          >
            {submitLabel}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
