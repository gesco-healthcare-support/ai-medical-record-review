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
import { BUNDLES } from "@/lib/bundle-api";
import type { HeaderFields } from "@/lib/review-api";
import { downloadFile } from "@/lib/download";
import { humanizeError } from "@/lib/errors";

const DEFAULT_QME = "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)";

/** Export dialog: the four report-header fields feed three outputs. "Export to Word" (POST
 *  /export -> .docx) is the summary letter alone; "Export to linked PDF" (POST /export/pdf) is the
 *  summary letter followed by the full source record, each summary title linking to its source
 *  page; "Download all" (POST /export/zip) is both of those plus a combined PDF for each category
 *  bundle that matches something in this record, which is the single hand-over the reviewers asked
 *  for instead of four separate clicks.
 *  Patient name / DOB / law firm prefill from the record's Auto-fill header when it has been run. */
export function ExportDialog({
  open,
  onOpenChange,
  documentId,
  includedCount,
  excludedCount,
  defaults,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  includedCount: number;
  excludedCount: number;
  defaults?: HeaderFields | null;
}>) {
  const [patient, setPatient] = useState("");
  const [dob, setDob] = useState("");
  const [qme, setQme] = useState(DEFAULT_QME);
  const [firm, setFirm] = useState("");
  // Off by default: "(Pages X-Y)" is a reviewing aid, so the presentable report is what you get
  // without thinking about it.
  const [withPages, setWithPages] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Prefill from Auto-fill header each time the dialog opens (without clobbering manual edits mid-
  // session: we only seed on open). Empty header fields leave the inputs blank.
  useEffect(() => {
    if (!open) return;
    setWithPages(false); // reset on every open, like the header fields below
    if (!defaults) return;
    const full = `${defaults.patient_first_name || ""} ${defaults.patient_last_name || ""}`.trim();
    setPatient(full);
    setDob(defaults.patient_dob || "");
    setFirm(defaults.law_firm || "");
  }, [open, defaults]);

  // All three export buttons share the header fields; the endpoint, the fallback filename and
  // (for the zip) the bundles to include are what differ.
  async function runExport(
    endpoint: string,
    fallbackName: string,
    extra: Record<string, unknown> = {},
  ) {
    setBusy(true);
    setError("");
    try {
      // Was its own fetch, which threw `export failed (500)` without reading the body - so the
      // server's actual reason (a 422 naming the document it could not read, a 503 naming the AI
      // outage) never reached this dialog, and a 401 RETURNED, closing nothing and reporting
      // nothing while the browser navigated away.
      await downloadFile(
        `/documents/${documentId}/${endpoint}`,
        {
          patientName: patient,
          patientdob: dob,
          QMEorAME: qme,
          lawfirm: firm,
          includePageNumbers: withPages,
          ...extra,
        },
        fallbackName,
      );
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
          <label className="ev-check" htmlFor="expPages">
            <input
              id="expPages"
              type="checkbox"
              checked={withPages}
              onChange={(e) => setWithPages(e.target.checked)}
            />{" "}
            Include page numbers after each title, e.g. (Pages 3-5)
          </label>
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
            className="ev-btn ev-btn-ghost"
            onClick={() => runExport("export/pdf", "record_linked.pdf")}
            disabled={busy}
          >
            {busy ? "Preparing..." : "Export to linked PDF"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={() =>
              runExport("export/zip", "record.zip", {
                // `label` carries the SLUG, matching what the bundle page itself sends, so a
                // member of the archive is named exactly as its standalone download would be.
                bundles: BUNDLES.map((b) => ({ label: b.slug, categories: b.categories })),
              })
            }
            disabled={busy}
          >
            {busy ? "Preparing..." : "Download all (.zip)"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
