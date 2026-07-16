"use client";

import { useEffect, useRef, useState } from "react";
import { useDocuments, useUploadDocument } from "@/hooks/use-documents";
import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { ApiError } from "@/lib/api";
import {
  downloadBundlePdf,
  downloadBundleSummary,
  type BundleConfig,
} from "@/lib/bundle-api";
import { DocumentsTable } from "@/components/documents/documents-table";
import { StartPanel } from "@/components/review/start-panel";
import { ProgressPanel } from "@/components/review/progress-panel";
import { ReviewEditor } from "@/components/review/review-editor";

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}
function errMessage(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback;
}

/** Category-bundle workspace: pick or upload a record, review it with the shared editor, then
 *  download or summarize just the documents whose category is in this page's set. One component,
 *  two configs (Diagnostic & Operative / Depositions). */
export function BundlePageClient({ config }: { config: BundleConfig }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pickerMsg, setPickerMsg] = useState("");
  const [patient, setPatient] = useState("");
  const [dob, setDob] = useState("");
  const [qme, setQme] = useState("");
  const [firm, setFirm] = useState("");
  const [resultMsg, setResultMsg] = useState("");
  const [working, setWorking] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);
  const { data: docs = [] } = useDocuments();
  const upload = useUploadDocument();
  const wf = useReviewWorkflow(selectedId, { enableSummaries: false });

  // The DS stacks the picker/editor/action card in one scrolling main; that layout keys off the
  // body attribute (evaluators-ds.css: body[data-bundle-slug]).
  useEffect(() => {
    document.body.dataset.bundleSlug = config.slug;
    return () => {
      delete document.body.dataset.bundleSlug;
    };
  }, [config.slug]);

  const matched = wf.rows.filter((row) => config.categories.includes(String(row.category))).length;

  async function uploadSelected() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setPickerMsg("Choose a PDF first.");
      return;
    }
    if (!isPdf(file)) {
      setPickerMsg("Only PDF files can be uploaded.");
      return;
    }
    setPickerMsg("Uploading...");
    try {
      const created = await upload.mutateAsync(file);
      setPickerMsg("");
      if (fileRef.current) fileRef.current.value = "";
      setSelectedId(created.id);
    } catch (err) {
      setPickerMsg(errMessage(err, "Upload failed."));
    }
  }

  function chooseAnother() {
    setSelectedId(null);
    setResultMsg("");
    setPatient("");
    setDob("");
    setQme("");
    setFirm("");
  }

  async function download(kind: "pdf" | "summary") {
    if (!selectedId) return;
    setWorking(true);
    setResultMsg("Working...");
    try {
      if (kind === "pdf") {
        await downloadBundlePdf(selectedId, config);
      } else {
        await downloadBundleSummary(selectedId, config, {
          patientName: patient,
          patientdob: dob,
          QMEorAME: qme,
          lawfirm: firm,
        });
      }
      setResultMsg("Downloaded.");
    } catch (err) {
      setResultMsg(errMessage(err, "The download failed."));
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      {wf.banner ? <div className="banner">{wf.banner}</div> : null}
      <main>
        {!selectedId ? (
          <section className="hd-column">
            <div className="hd-header">
              <div>
                <h1>{config.label} builder</h1>
                <p className="muted bundle-lead">
                  Upload a new record or pick an existing one. Identify and correct its documents,
                  then download or summarize just the {config.label} records.
                </p>
              </div>
              <div className="bundle-upload">
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/pdf"
                  aria-label="PDF to upload"
                />
                <button
                  type="button"
                  className="ev-btn ev-btn-primary"
                  onClick={uploadSelected}
                  disabled={upload.isPending}
                >
                  {upload.isPending ? "Uploading..." : "Upload PDF"}
                </button>
              </div>
            </div>
            <DocumentsTable docs={docs} onOpen={(id) => setSelectedId(id)} />
            {pickerMsg ? <div className="error-text">{pickerMsg}</div> : null}
          </section>
        ) : (
          <>
            {wf.section === "start" ? (
              <StartPanel rerun={wf.rows.length > 0} hint={wf.startHint} onStart={wf.onStart} />
            ) : null}
            {wf.section === "progress" ? (
              <ProgressPanel
                title={wf.progress.title}
                pct={wf.progress.pct}
                detail={wf.progress.detail}
              />
            ) : null}
            {wf.section === "editor" ? (
              <>
                <ReviewEditor
                  documentId={selectedId}
                  filename={wf.filename}
                  rows={wf.rows}
                  categories={wf.categories}
                  totalPages={wf.totalPages}
                  saveState={wf.saveState}
                  onRowsChange={wf.onRowsChange}
                  showSummarize={false}
                />
                <section id="bundle-actions">
                  <div className="bundle-actions-inner">
                    <div className="hd-card bundle-card">
                      <h2>{config.label} records</h2>
                      <p className="muted" id="bundleActionsHint">
                        {matched
                          ? `${matched} ${config.label} document${matched === 1 ? "" : "s"} found in this record.`
                          : `No ${config.label} documents in this record yet - fix categories above if that looks wrong.`}
                      </p>
                      <div className="bundle-fields">
                        <div className="ev-dialog-row">
                          <div className="ev-field-2">
                            <label className="ev-lbl" htmlFor="bundlePatient">
                              Patient name
                            </label>
                            <input
                              id="bundlePatient"
                              className="ev-inp"
                              placeholder="Full name"
                              value={patient}
                              onChange={(e) => setPatient(e.target.value)}
                            />
                          </div>
                          <div className="ev-field-1">
                            <label className="ev-lbl" htmlFor="bundleDob">
                              DOB
                            </label>
                            <input
                              id="bundleDob"
                              className="ev-inp"
                              placeholder="MM/DD/YYYY"
                              value={dob}
                              onChange={(e) => setDob(e.target.value)}
                            />
                          </div>
                        </div>
                        <div>
                          <label className="ev-lbl" htmlFor="bundleQme">
                            Evaluation type (QME / AME)
                          </label>
                          <input
                            id="bundleQme"
                            className="ev-inp"
                            placeholder="e.g. PANEL QUALIFIED MEDICAL EVALUATION"
                            value={qme}
                            onChange={(e) => setQme(e.target.value)}
                          />
                        </div>
                        <div>
                          <label className="ev-lbl" htmlFor="bundleFirm">
                            Attorney law firm
                          </label>
                          <input
                            id="bundleFirm"
                            className="ev-inp"
                            placeholder="Firm name"
                            value={firm}
                            onChange={(e) => setFirm(e.target.value)}
                          />
                        </div>
                      </div>
                      <div className="bundle-buttons">
                        <button
                          type="button"
                          className="ev-btn ev-btn-outline"
                          onClick={() => download("pdf")}
                          disabled={matched === 0 || working}
                        >
                          Download combined PDF
                        </button>
                        <button
                          type="button"
                          className="ev-btn ev-btn-primary"
                          onClick={() => download("summary")}
                          disabled={matched === 0 || working}
                        >
                          Summarize to Word
                        </button>
                        <button
                          type="button"
                          className="ev-btn ev-btn-ghost"
                          onClick={chooseAnother}
                        >
                          Choose another document
                        </button>
                      </div>
                      <div className="muted" id="bundleResultMsg">
                        {resultMsg}
                      </div>
                    </div>
                  </div>
                </section>
              </>
            ) : null}
          </>
        )}
      </main>
    </>
  );
}
