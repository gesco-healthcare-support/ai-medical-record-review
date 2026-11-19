"use client";

import { useRef, useState } from "react";
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

type Item = { key: number; file: File; kind: ReceivedKind };

/** Put one record together from the files it arrived in (POST /api/documents/combine), the step the
 *  office used to do in Adobe Acrobat. The uploader gives the patient's name, ID, the day the
 *  records were received, the appointment date and who sent them, and adds the files; each gets a
 *  kind guessed from its name, and the list keeps them in the order a record is put together
 *  (`lib/received-files.ts`). A kind can be corrected and a file moved; the server joins them
 *  exactly as listed. Nothing starts Identify.
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
  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [dateReceived, setDateReceived] = useState("");
  const [appointmentDate, setAppointmentDate] = useState("");
  const [recordsFrom, setRecordsFrom] = useState<RecordsFrom | "">("");
  const [items, setItems] = useState<Item[]>([]);
  // What the uploader typed in the Patient ID box, or null while they have not touched it - the box
  // then shows the ID the files' names agree on.
  const [typedId, setTypedId] = useState<string | null>(null);
  const nextKey = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const foundId = agreedPatientId(items.map((item) => item.file.name)) ?? "";
  const patientId = cleanPatientId(typedId ?? foundId);
  const idTooLong = patientId.length > PATIENT_ID_MAX_LENGTH;
  const ready =
    items.length > 0 &&
    lastName.trim() !== "" &&
    firstName.trim() !== "" &&
    patientId !== "" &&
    !idTooLong &&
    dateReceived !== "" &&
    appointmentDate !== "" &&
    recordsFrom !== "";

  /** Dismissing clears everything staged, whichever control did it (one function, so Cancel and
   *  Escape cannot drift apart again - #264's shape). */
  function close() {
    setLastName("");
    setFirstName("");
    setDateReceived("");
    setAppointmentDate("");
    setRecordsFrom("");
    setItems([]);
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
    const added = pdfs.map((file) => ({
      key: nextKey.current++,
      file,
      kind: guessKind(file.name),
    }));
    setItems((prev) => inReceivedOrder([...prev, ...added]));
    if (fileInput.current) fileInput.current.value = "";
  }

  function setKind(key: number, kind: ReceivedKind) {
    setItems((prev) => inReceivedOrder(prev.map((it) => (it.key === key ? { ...it, kind } : it))));
  }

  function move(index: number, step: -1 | 1) {
    setItems((prev) => {
      const next = [...prev];
      [next[index], next[index + step]] = [next[index + step], next[index]];
      return next;
    });
  }

  function remove(key: number) {
    setItems((prev) => prev.filter((it) => it.key !== key));
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
        files: items.map(({ file, kind }) => ({ file, kind })),
        owner: owner?.id ?? null,
      });
      const others = otherRecordsLine(created.patient_records);
      const files = items.length === 1 ? "1 file" : `${items.length} files`;
      const made = `One record made from ${files}, ${created.page_count} pages`;
      toast.success(
        (owner ? `${made}, in ${owner.name}'s records.` : `${made}.`) +
          (others ? ` ${others}` : ""),
      );
      close();
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
            Add the files the records came in. They are put together into one record in the order
            listed: advocacy letter, declaration, medical index, medical records, proof of service,
            internal email. Change a file&apos;s kind or move it if it belongs elsewhere.
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
                  <li
                    key={item.key}
                    className="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm"
                  >
                    <span className="w-5 shrink-0 text-right text-muted-foreground">
                      {index + 1}.
                    </span>
                    <span className="min-w-0 flex-1 truncate">{item.file.name}</span>
                    <select
                      className="ev-inp w-auto"
                      aria-label={`What ${item.file.name} is`}
                      value={item.kind}
                      onChange={(e) => setKind(item.key, e.target.value as ReceivedKind)}
                    >
                      {RECEIVED_KINDS.map((kind) => (
                        <option key={kind} value={kind}>
                          {RECEIVED_KIND_LABELS[kind]}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      aria-label={`Move ${item.file.name} up`}
                      className="text-gray-500 hover:text-gray-900 disabled:opacity-30"
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${item.file.name} down`}
                      className="text-gray-500 hover:text-gray-900 disabled:opacity-30"
                      disabled={index === items.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${item.file.name}`}
                      className="text-gray-400 hover:text-danger"
                      onClick={() => remove(item.key)}
                    >
                      <X className="size-4" aria-hidden />
                    </button>
                  </li>
                ))}
              </ol>
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
