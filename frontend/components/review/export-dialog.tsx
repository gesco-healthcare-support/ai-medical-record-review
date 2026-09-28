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
import { type DownloadWatch, useDownloadWatch } from "@/hooks/use-download-watch";
import { downloadFile, type PreparedDownload } from "@/lib/download";
import { humanizeError } from "@/lib/errors";

const DEFAULT_QME = "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)";

/** Export dialog: the report-header fields feed four outputs. "Export to Word" (POST /export
 *  -> .docx) is the summary letter alone; "Export to linked PDF" (POST /export/pdf) is the summary
 *  letter followed by the full source record, each summary title linking to its source page;
 *  "Download memo" (POST /export/memo) is the covering note to the doctor's office, which leads
 *  with the cover sheet's page count against the file's own and then repeats the SAME page
 *  accounting the letter closes with; and "Download all" (POST /export/zip) is every one of those
 *  in one archive plus a combined PDF for each category bundle that matches this record, which is
 *  the single hand-over the reviewers asked for instead of four separate clicks.
 *
 *  The memo asks for NOTHING extra here. Its doctor comes from the review page's dropdown and the
 *  reviewer signing it from the account - a first version added a "Doctor (memo only)" box and a
 *  "Documents received on" box to this dialog, which duplicated a field that already exists and
 *  let the memo disagree with the report downloaded beside it.
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
  // The download handed to the browser, watched until its outcome is known (#390). The dialog stays open
  // meanwhile - the page cannot see the download itself - and closing it stops the watching.
  const [handedOver, setHandedOver] = useState<PreparedDownload | null>(null);
  const watch = useDownloadWatch(handedOver);
  const locked = busy || watch.watching;

  useEffect(() => {
    if (!open) setHandedOver(null);
  }, [open]);

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
    setHandedOver(null);
    try {
      // Was its own fetch, which threw `export failed (500)` without reading the body - so the
      // server's actual reason (a 422 naming the document it could not read, a 503 naming the AI
      // outage) never reached this dialog, and a 401 RETURNED, closing nothing and reporting
      // nothing while the browser navigated away.
      const prepared = await downloadFile(
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
      setHandedOver(prepared);
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
          {footerNote(error, watch)}
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
            disabled={locked}
          >
            {busy ? "Preparing..." : "Export to Word"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-ghost"
            onClick={() => runExport("export/memo", "memo.docx")}
            disabled={locked}
          >
            {busy ? "Preparing..." : "Download memo"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={() => runExport("export/pdf", "record_linked.pdf")}
            disabled={locked}
          >
            {busy ? "Preparing..." : "Export to linked PDF"}
          </button>
          <button
            type="button"
            className="ev-btn ev-btn-primary"
            onClick={() =>
              runExport("export/zip", "record.zip", {
                // `label` carries the SLUG and `downloadName` the reader-facing name, matching
                // what the bundle page itself sends, so a member of the archive is named exactly
                // as its standalone download would be.
                bundles: BUNDLES.map((b) => ({
                  label: b.slug,
                  categories: b.categories,
                  coverHeading: b.coverHeading,
                  downloadName: b.downloadName,
                })),
              })
            }
            disabled={locked}
          >
            {busy ? "Preparing..." : "Download all (.zip)"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The footer's one sentence: an export error, else what the watched download is doing (#390). */
function footerNote(error: string, watch: DownloadWatch) {
  // ALWAYS on the page, empty when there is nothing to say: a screen reader announces changes to a live
  // region it is already watching, and one that appears together with its first sentence is not reliably
  // announced (#390 review). `<output>` is a live status region by itself - no `role` needed.
  const problem = Boolean(error) || watch.tone === "err";
  return (
    <output className={`${problem ? "error-text" : "muted"} mr-auto`}>
      {error || watch.message}
    </output>
  );
}
