"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cleanPatientId, PATIENT_ID_MAX_LENGTH } from "@/lib/patient-id";

/** Asked before an upload whose file name carries no patient ID (or two): the uploader types it.
 *  The office names record files after the patient, with the ID in the name; when it is not there
 *  the record would otherwise never be matched to the patient's other records. Nothing is sent
 *  until an ID is given; Cancel uploads nothing. */
export function PatientIdDialog({
  fileName,
  onCancel,
  onSubmit,
}: Readonly<{
  /** The file waiting for its ID, or null when the dialog is closed. */
  fileName: string | null;
  onCancel: () => void;
  onSubmit: (patientId: string) => void;
}>) {
  const [value, setValue] = useState("");
  // A new file starts with an empty box, never the last file's ID.
  useEffect(() => {
    setValue("");
  }, [fileName]);
  const cleaned = cleanPatientId(value);
  const tooLong = cleaned.length > PATIENT_ID_MAX_LENGTH;
  const ready = cleaned.length > 0 && !tooLong;

  return (
    <Dialog open={fileName !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Patient ID needed</DialogTitle>
          <DialogDescription>
            We could not find the patient ID in the file name &quot;{fileName}&quot;. Enter it to
            upload the record - it finds this patient&apos;s other records.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) onSubmit(cleaned);
          }}
        >
          <label className="ev-lbl" htmlFor="uploadPatientId">
            Patient ID
          </label>
          <input
            id="uploadPatientId"
            className="ev-inp"
            autoComplete="off"
            placeholder="e.g. the account number on the letter"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {tooLong ? (
            <output className="rc-hb-warn">
              A patient ID is at most {PATIENT_ID_MAX_LENGTH} letters and digits.
            </output>
          ) : null}
          <DialogFooter>
            <button type="button" className="ev-btn ev-btn-outline" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="ev-btn ev-btn-primary" disabled={!ready}>
              Upload
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
