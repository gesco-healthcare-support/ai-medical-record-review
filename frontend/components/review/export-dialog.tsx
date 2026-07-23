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
import type { HeaderFields } from "@/lib/review-api";
import { humanizeError } from "@/lib/errors";

const DEFAULT_QME = "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)";

/** Export dialog: the four report-header fields feed two outputs. "Export to Word" (POST /export
 *  -> .docx) is the summary letter alone; "Export to linked PDF" (POST /export/pdf) is the summary
 *  letter followed by the full source record, each summary title linking to its source page.
 *  Patient name / DOB / law firm prefill from the record's Auto-fill header when it has been run. */
export function ExportDialog({
  open,
  onOpenChange,
  documentId,
  includedCount,
  excludedCount,
  defaults,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  includedCount: number;
  excludedCount: number;
  defaults?: HeaderFields | null;
}) {
  const [patient, setPatient] = useState("");
  const [dob, setDob] = useState("");
  const [qme, setQme] = useState(DEFAULT_QME);
  const [firm, setFirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Prefill from Auto-fill header each time the dialog opens (without clobbering manual edits mid-
  // session: we only seed on open). Empty header fields leave the inputs blank.
  useEffect(() => {
    if (!open || !defaults) return;
    const full = `${defaults.patient_first_name || ""} ${defaults.patient_last_name || ""}`.trim();
    setPatient(full);
    setDob(defaults.patient_dob || "");
    setFirm(defaults.law_firm || "");
  }, [open, defaults]);

  // Both export buttons share the header fields; only the endpoint + fallback filename differ.
  async function runExport(endpoint: string, fallbackName: string) {
    setBusy(true);
    setError("");
    try {
      const resp = await fetch(`/api/documents/${documentId}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          patientName: patient,
          patientdob: dob,
          QMEorAME: qme,
          lawfirm: firm,
        }),
      });
      if (resp.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (!resp.ok) throw new Error(`export failed (${resp.status})`);
      const cd = resp.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename="?([^"]+)"?/);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = match ? match[1] : fallbackName;
      link.click();
      URL.revokeObjectURL(url);
      onOpenChange(false);
    } catch (err) {
      setError(humanizeError(err, { fallback: "Export failed." }));
    } finally {
      setBusy(false);
    }
  }

  const plural = (n: number) => `${n} summar${n === 1 ? "y" : "ies"}`;
  const note =
    `These details fill the report header. ${plural(includedCount)} will be exported` +
    (excludedCount ? `; ${excludedCount} excluded` : "") +
    ". Enter only what the report requires - no additional PHI.";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Export</DialogTitle>
          <DialogDescription>{note}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="flex gap-3">
            <div className="grid flex-[2] gap-1.5">
              <label className="ev-lbl" htmlFor="expPatient">
                Patient name
              </label>
              <input
                id="expPatient"
                className="ev-inp"
                placeholder="Full name"
                value={patient}
                onChange={(e) => setPatient(e.target.value)}
              />
            </div>
            <div className="grid flex-1 gap-1.5">
              <label className="ev-lbl" htmlFor="expDob">
                DOB
              </label>
              <input
                id="expDob"
                className="ev-inp"
                placeholder="MM/DD/YYYY"
                value={dob}
                onChange={(e) => setDob(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="expQme">
              Evaluation type (QME / AME)
            </label>
            <input
              id="expQme"
              className="ev-inp"
              value={qme}
              onChange={(e) => setQme(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="expFirm">
              Attorney law firm
            </label>
            <input
              id="expFirm"
              className="ev-inp"
              placeholder="Firm name"
              value={firm}
              onChange={(e) => setFirm(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter className="flex-wrap">
          {error ? <span className="error-text mr-auto">{error}</span> : null}
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => runExport("export", "summaries.docx")}
            disabled={busy}
          >
            {busy ? "Preparing..." : "Export to Word"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={() => runExport("export/pdf", "record_linked.pdf")}
            disabled={busy}
          >
            {busy ? "Preparing..." : "Export to linked PDF"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
