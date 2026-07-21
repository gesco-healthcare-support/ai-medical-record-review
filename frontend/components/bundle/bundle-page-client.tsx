"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, FileText } from "lucide-react";
import { useDocuments } from "@/hooks/use-documents";
import { extractHeader, getDocument } from "@/lib/review-api";
import { ApiError } from "@/lib/api";
import {
  downloadBundlePdf,
  downloadBundleSummary,
  type BundleConfig,
} from "@/lib/bundle-api";
import { StatusPill } from "@/components/documents/status-pill";
import { SegmentedTabs } from "@/components/ui/segmented-tabs";
import type { CategoryOption } from "@/lib/types";

const DEFAULT_QME = "PANEL QUALIFIED MEDICAL EVALUATION (ML-10*-)";

// The two bundle entries share one screen; the SegmentedTabs navigate between their routes. The
// tab value is the config slug so the active tab matches whichever page is mounted.
const BUNDLE_TABS = [
  { value: "diagnostic-operative", label: "Diagnostic & Operative", href: "/diagnostics" },
  { value: "depositions", label: "Depositions", href: "/depositions" },
] as const;

function errMessage(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback;
}

function categoryLabel(categories: CategoryOption[], id: string) {
  const found = categories.find((c) => String(c.id) === String(id));
  return found ? `${found.id} - ${found.name}` : String(id);
}

/** Category-bundle workspace (DS §5): pick an already-identified record, see the documents whose
 *  category is in this page's set (read-only), then download a combined PDF or summarize just those
 *  to Word. One component, two configs (Diagnostic & Operative / Depositions) switched via tabs. */
export function BundlePageClient({ config }: { config: BundleConfig }) {
  const router = useRouter();
  const { data: docs = [] } = useDocuments();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["document", selectedId],
    queryFn: () => getDocument(selectedId as string),
    enabled: Boolean(selectedId),
  });

  // Aside form + per-action state.
  const [patient, setPatient] = useState("");
  const [dob, setDob] = useState("");
  const [qme, setQme] = useState(DEFAULT_QME);
  const [firm, setFirm] = useState("");
  const [autoFilling, setAutoFilling] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [sumBusy, setSumBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "ok" | "err" | ""; msg: string }>({ kind: "", msg: "" });

  const rows = detail?.rows ?? [];
  const categories = detail?.categories ?? [];
  const matches = rows.filter((row) => config.categories.includes(String(row.category)));
  const identified = rows.length > 0;

  const pickerDocs = [...docs].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  function chooseAnother() {
    setSelectedId(null);
    setResult({ kind: "", msg: "" });
    setPatient("");
    setDob("");
    setQme(DEFAULT_QME);
    setFirm("");
  }

  async function autoFill() {
    if (!selectedId) return;
    setAutoFilling(true);
    try {
      const fields = await extractHeader(selectedId);
      const full = `${fields.patient_first_name || ""} ${fields.patient_last_name || ""}`.trim();
      setPatient(full);
      setDob(fields.patient_dob || "");
      setFirm(fields.law_firm || "");
      setResult({ kind: "ok", msg: "Header details filled from the record." });
    } catch (err) {
      setResult({ kind: "err", msg: errMessage(err, "Could not read the header.") });
    } finally {
      setAutoFilling(false);
    }
  }

  async function downloadPdf() {
    if (!selectedId) return;
    setPdfBusy(true);
    setResult({ kind: "", msg: "Combining pages..." });
    try {
      await downloadBundlePdf(selectedId, config);
      setResult({ kind: "ok", msg: "Combined PDF downloaded." });
    } catch (err) {
      setResult({ kind: "err", msg: errMessage(err, "The download failed.") });
    } finally {
      setPdfBusy(false);
    }
  }

  async function summarize() {
    if (!selectedId) return;
    setSumBusy(true);
    setResult({ kind: "", msg: "Preparing report..." });
    try {
      await downloadBundleSummary(selectedId, config, {
        patientName: patient,
        patientdob: dob,
        QMEorAME: qme,
        lawfirm: firm,
      });
      setResult({ kind: "ok", msg: "Word report downloaded." });
    } catch (err) {
      setResult({ kind: "err", msg: errMessage(err, "The report failed.") });
    } finally {
      setSumBusy(false);
    }
  }

  type BundleSlug = (typeof BUNDLE_TABS)[number]["value"];
  const tabs = (
    <SegmentedTabs
      tabs={BUNDLE_TABS.map((t) => ({ value: t.value, label: t.label }))}
      value={config.slug as BundleSlug}
      onValueChange={(value) => {
        const target = BUNDLE_TABS.find((t) => t.value === value);
        if (target && target.value !== config.slug) router.push(target.href);
      }}
      ariaLabel="Bundle type"
    />
  );

  return (
    <main className="bnd-main">
      <div className="bnd-column">
        {!selectedId ? (
          <>
            <div className="bnd-head">
              <div className="bnd-heading">
                <h1>{config.label} builder</h1>
                <p className="bnd-lead">
                  Pick a record you have already identified. You will see the {config.label}{" "}
                  documents in it, then download a combined PDF or summarize just those to Word.
                </p>
              </div>
              {tabs}
            </div>

            <div className="hd-card">
              <table className="hd-table">
                <thead>
                  <tr>
                    <th>Record name</th>
                    <th className="hd-w-pages">Pages</th>
                    <th className="hd-w-status">Status</th>
                    <th className="hd-w-uploaded">Uploaded</th>
                    <th className="hd-w-menu" aria-label="Select" />
                  </tr>
                </thead>
                <tbody>
                  {pickerDocs.length === 0 ? (
                    <tr className="hd-norows">
                      <td colSpan={5}>No records yet. Upload one from My documents first.</td>
                    </tr>
                  ) : (
                    pickerDocs.map((doc) => (
                      <tr key={doc.id} onClick={() => setSelectedId(doc.id)}>
                        <td>
                          <span className="hd-doc">
                            <FileText width={15} height={15} aria-hidden />
                            <span className="hd-name">{doc.original_filename}</span>
                          </span>
                        </td>
                        <td className="hd-muted">{doc.page_count}</td>
                        <td>
                          <StatusPill doc={doc} />
                        </td>
                        <td className="hd-muted">
                          {new Date(doc.created_at).toLocaleDateString()}
                        </td>
                        <td className="bnd-selectcell" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="ev-btn ev-btn-outline ev-btn-sm"
                            onClick={() => setSelectedId(doc.id)}
                          >
                            Select
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <>
            <div className="bnd-head">
              <div className="bnd-breadcrumb">
                <button type="button" className="bnd-linkbtn" onClick={chooseAnother}>
                  <ArrowLeft width={14} height={14} aria-hidden /> Choose another record
                </button>
                <span className="bnd-crumb-sep">·</span>
                <span className="bnd-crumb-name">{detail?.original_filename || "Record"}</span>
                {identified ? (
                  <>
                    <span className="bnd-crumb-sep">·</span>
                    <Link className="bnd-linkbtn" href={`/records/${selectedId}`}>
                      Fix categories in Review &amp; correct{" "}
                      <ArrowRight width={14} height={14} aria-hidden />
                    </Link>
                  </>
                ) : null}
              </div>
              {tabs}
            </div>

            {detailLoading ? null : !identified ? (
              <div className="bnd-empty">
                <p className="bnd-empty-title">This record hasn&apos;t been identified yet</p>
                <p>
                  Identify its documents in Review &amp; correct first, then come back to build the{" "}
                  {config.label} bundle.
                </p>
                <div className="bundle-buttons" style={{ justifyContent: "center", marginTop: 14 }}>
                  <button type="button" className="ev-btn ev-btn-ghost" onClick={chooseAnother}>
                    Choose another record
                  </button>
                  <Link className="ev-btn ev-btn-primary" href={`/records/${selectedId}`}>
                    Open in Review &amp; correct
                  </Link>
                </div>
              </div>
            ) : (
              <div className="bnd-grid">
                <div className="hd-card">
                  <div className="bnd-card-head">
                    {matches.length} matching document{matches.length === 1 ? "" : "s"}
                  </div>
                  {matches.length === 0 ? (
                    <div className="bnd-empty" style={{ border: "none" }}>
                      <p className="bnd-empty-title">No {config.label} documents here</p>
                      <p>
                        That&apos;s normal - not every record has them. Check the categories in
                        Review &amp; correct if you expected some.
                      </p>
                    </div>
                  ) : (
                    <table className="hd-table">
                      <thead>
                        <tr>
                          <th className="hd-w-pages">Pages</th>
                          <th>Title</th>
                          <th className="hd-w-status">Category</th>
                          <th className="hd-w-uploaded">Date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {matches.map((row, i) => (
                          <tr key={i}>
                            <td className="hd-muted">
                              {row.start}
                              {"–"}
                              {row.end}
                            </td>
                            <td>
                              <span className="hd-name">
                                {row.title && row.title !== "-" ? row.title : "(untitled document)"}
                              </span>
                            </td>
                            <td className="hd-muted">{categoryLabel(categories, row.category)}</td>
                            <td className="hd-muted">
                              {row.date && row.date !== "-" ? row.date : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>

                <aside className="hd-card bundle-card">
                  <div>
                    <h2>Build the bundle</h2>
                    <p className="muted" style={{ margin: "2px 0 0" }}>
                      Download the matched pages, or summarize just those documents to a Word report.
                    </p>
                  </div>
                  <button
                    type="button"
                    className="ev-btn ev-btn-ghost ev-btn-sm"
                    style={{ alignSelf: "flex-start" }}
                    onClick={autoFill}
                    disabled={autoFilling}
                  >
                    {autoFilling ? "Reading..." : "Auto-fill header from record"}
                  </button>
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
                      onClick={downloadPdf}
                      disabled={matches.length === 0 || pdfBusy || sumBusy}
                    >
                      {pdfBusy ? "Combining pages..." : "Download combined PDF"}
                    </button>
                    <button
                      type="button"
                      className="ev-btn ev-btn-primary"
                      onClick={summarize}
                      disabled={matches.length === 0 || pdfBusy || sumBusy}
                    >
                      {sumBusy ? "Preparing report..." : "Summarize to Word"}
                    </button>
                  </div>
                  <div className={`bnd-result ${result.kind}`}>{result.msg}</div>
                </aside>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
