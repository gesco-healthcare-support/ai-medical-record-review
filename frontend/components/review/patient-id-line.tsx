"use client";

import { useEffect, useState } from "react";
import { setPatientId } from "@/lib/documents-api";
import { humanizeError } from "@/lib/errors";
import { cleanPatientId, otherRecordsLine, PATIENT_ID_MAX_LENGTH } from "@/lib/patient-id";
import { cn } from "@/lib/utils";

/** The record's patient ID and how many other records carry it, on every tab of the workbench.
 *
 *  The ID comes from the upload's file name or the uploader; here a reviewer can fill one in that
 *  is missing or correct a wrong one. It saves on Enter or when the box loses focus (PUT
 *  /patient-id), on its own route rather than with the header, whose autosave an older open tab
 *  would use to blank it. The count is every OTHER record with the same ID, in any reviewer's
 *  account - a patient reviewed before. */
export function PatientIdLine({
  documentId,
  patientId,
  others,
  onSaved,
}: Readonly<{
  documentId: string;
  /** The stored ID, "" for none. */
  patientId: string;
  others: number;
  onSaved: (saved: { id: string; others: number }) => void;
}>) {
  const [value, setValue] = useState(patientId);
  const [state, setState] = useState<{ kind: "" | "saving" | "error"; message?: string }>({
    kind: "",
  });
  // The stored ID changing (another record, a reload) replaces the box's text.
  useEffect(() => {
    setValue(patientId);
    setState({ kind: "" });
  }, [patientId, documentId]);

  async function save() {
    const next = cleanPatientId(value);
    if (next === patientId) {
      setValue(patientId);
      return;
    }
    if (next.length > PATIENT_ID_MAX_LENGTH) {
      setState({
        kind: "error",
        message: `A patient ID is at most ${PATIENT_ID_MAX_LENGTH} letters and digits.`,
      });
      return;
    }
    setState({ kind: "saving", message: "Saving..." });
    try {
      const saved = await setPatientId(documentId, next);
      setState({ kind: "" });
      onSaved({ id: saved.patient_id, others: saved.patient_records });
    } catch (err) {
      setState({
        kind: "error",
        message: `Not saved: ${humanizeError(err, { fallback: "please try again" })}`,
      });
    }
  }

  let note = "No other records for this patient.";
  if (!patientId) note = "No patient ID yet - enter it to find this patient's other records.";
  else if (others > 0) note = otherRecordsLine(others);

  return (
    <div className="rc-headerbar">
      <div className="rc-hb-fields">
        <label className="rc-hb-field">
          <span className="ev-lbl">Patient ID</span>
          <input
            className="ev-inp"
            autoComplete="off"
            value={value}
            placeholder="Not given"
            onChange={(e) => setValue(e.target.value)}
            onBlur={() => void save()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save();
            }}
          />
        </label>
      </div>
      <div className="rc-hb-actions">
        {state.kind ? (
          <span className={cn("rc-save", state.kind === "saving" ? "dirty" : "error")}>
            {state.message}
          </span>
        ) : null}
        <output className={cn(!patientId && "rc-hb-warn", patientId && others > 0 && "font-medium")}>
          {note}
        </output>
      </div>
    </div>
  );
}
