"use client";

import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { humanizeError } from "@/lib/errors";
import { extractHeader, saveHeader, type HeaderFields } from "@/lib/review-api";

const EMPTY: HeaderFields = {
  patient_first_name: "",
  patient_last_name: "",
  patient_dob: "",
  law_firm: "",
  attorney_name: "",
  doctor: "",
  letter_type: "",
  letter_date: "",
  pages_received: "",
};

/** The covering letter that arrived with the records. The value is what the backend stores
 *  (reporting.LETTER_TYPES); the label is what the reviewer reads. "None" is a real answer,
 *  not an empty one - plenty of records arrive with no letter and the opening paragraph then
 *  leaves the clause out. */
const LETTER_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "advocacy", label: "Advocacy letter" },
  { value: "interrogatory", label: "Interrogatory letter" },
  { value: "none", label: "No letter" },
] as const;

/**
 * A warning for a "Pages received" figure that is implausible against the file's own page count,
 * or null. Issue #330: a reviewer who types 500 for 50 produces a client letter and memo stating
 * 500 pages, and nothing anywhere says so - the server's own guard catches only a figure too SMALL
 * for the rows it covers (24 for 244), by design.
 *
 * Quiet on the routine case: in real deliverables the file runs a few pages LONGER than the cover
 * sheet (311/309, 293/290, 244/241, 229/226), because pages are attached downstream. So it speaks
 * only past the larger of 10 pages and a tenth of the file. A prompt, never a gate: saving is not
 * blocked, because the reviewer may be right and the file wrong.
 */
export function pageCountWarning(typed: string, filePages?: number): string | null {
  const value = typed.trim();
  if (!filePages || !/^\d+$/.test(value)) return null;
  const stated = Number(value);
  if (stated <= 0) return null;
  const margin = Math.max(10, Math.ceil(filePages / 10));
  if (Math.abs(stated - filePages) <= margin) return null;
  return `The file has ${filePages} pages; ${stated} is far from that. Check the cover sheet - it saves as typed.`;
}

/** Editable report-header bar shown on Review & correct and Summaries: patient first/last name, DOB,
 *  and law firm. Auto-fill / Re-detect extracts from the record AND persists it in one action (no
 *  separate Save needed); manual edits still save explicitly via PUT /header. Persisted values feed
 *  the export filename + header and are shared across pages through the parent's header state. */
export function HeaderBar({
  documentId,
  header,
  onSaved,
  doctors = [],
  pageCount,
}: Readonly<{
  documentId: string;
  header: HeaderFields | null;
  onSaved: (fields: HeaderFields) => void;
  /** The PDF's own page count, to check a typed "Pages received" against (#330). */
  pageCount?: number;
  /** Served with the record. Empty until it loads, which is why the select always carries
   *  the stored value as its own option - a saved doctor must not vanish from the box while
   *  the list is in flight, or a blur would write the record's doctor away. */
  doctors?: readonly string[];
}>) {
  const [fields, setFields] = useState<HeaderFields>(header ?? EMPTY);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [autoFilling, setAutoFilling] = useState(false);

  // Re-seed when the persisted header changes (e.g. after identify) unless the user is mid-edit.
  useEffect(() => {
    if (!dirty) setFields(header ?? EMPTY);
  }, [header, dirty]);

  function set(key: keyof HeaderFields, value: string) {
    setFields((f) => ({ ...f, [key]: value }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    try {
      await saveHeader(documentId, fields);
      setDirty(false);
      onSaved(fields);
      toast.success("Header saved.");
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not save the header." }));
    } finally {
      setSaving(false);
    }
  }

  async function autoFill() {
    setAutoFilling(true);
    try {
      const detected = await extractHeader(documentId);
      // extractHeader persists server-side; reflect it as the shared saved header (no Save step).
      // The reply holds only the four detected fields, so it is merged over the STORED header: the
      // other five are whatever the server still has. Replacing the header with the reply left
      // them undefined, and the next manual Save then stored each of them as "".
      const saved = { ...(header ?? EMPTY), ...detected };
      setFields(saved);
      setDirty(false);
      onSaved(saved);
      toast.success("Header detected and saved.");
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not read the header." }));
    } finally {
      setAutoFilling(false);
    }
  }

  // Once any header value is stored, the button re-detects (overwrites) rather than first-fills.
  const hasHeader = Boolean(
    header &&
      (header.patient_first_name ||
        header.patient_last_name ||
        header.patient_dob ||
        header.law_firm),
  );
  // The stored doctor is always offered, even when the served list has not arrived or no
  // longer contains them. Rendering a <select> whose value is absent from its options makes
  // the browser show the first option instead, and the next save would write that away.
  const doctorOptions = Array.from(
    new Set([...doctors, fields.doctor].filter(Boolean)),
  );

  const pagesWarning = pageCountWarning(fields.pages_received, pageCount);
  const pagesWarningId = useId();

  let autoFillLabel = "Auto-fill";
  if (autoFilling) autoFillLabel = "Reading...";
  else if (hasHeader) autoFillLabel = "Re-detect";

  return (
    <div className="rc-headerbar">
      <div className="rc-hb-fields">
        <label className="rc-hb-field">
          <span className="ev-lbl">First name</span>
          <input
            className="ev-inp"
            value={fields.patient_first_name}
            onChange={(e) => set("patient_first_name", e.target.value)}
            placeholder="First"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Last name</span>
          <input
            className="ev-inp"
            value={fields.patient_last_name}
            onChange={(e) => set("patient_last_name", e.target.value)}
            placeholder="Last"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">DOB</span>
          <input
            className="ev-inp"
            value={fields.patient_dob}
            onChange={(e) => set("patient_dob", e.target.value)}
            placeholder="MM/DD/YYYY"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Attorney</span>
          <input
            className="ev-inp"
            value={fields.attorney_name}
            onChange={(e) => set("attorney_name", e.target.value)}
            placeholder="Person who sent them"
          />
        </label>
        <label className="rc-hb-field rc-hb-firm">
          <span className="ev-lbl">Law firm</span>
          <input
            className="ev-inp"
            value={fields.law_firm}
            onChange={(e) => set("law_firm", e.target.value)}
            placeholder="Firm name"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Doctor</span>
          <select
            className="ev-inp"
            value={fields.doctor}
            onChange={(e) => set("doctor", e.target.value)}
          >
            <option value="">Not set</option>
            {doctorOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Letter</span>
          <select
            className="ev-inp"
            value={fields.letter_type}
            onChange={(e) => set("letter_type", e.target.value)}
          >
            {LETTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Letter date</span>
          <input
            className="ev-inp"
            value={fields.letter_date}
            onChange={(e) => set("letter_date", e.target.value)}
            placeholder="MM/DD/YYYY"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Pages received</span>
          <input
            className="ev-inp"
            value={fields.pages_received}
            onChange={(e) => set("pages_received", e.target.value)}
            placeholder="From cover sheet"
            aria-label="Pages received"
            aria-describedby={pagesWarning ? pagesWarningId : undefined}
          />
          {pagesWarning ? (
            <output id={pagesWarningId} className="rc-hb-warn">
              {pagesWarning}
            </output>
          ) : null}
        </label>
      </div>
      <div className="rc-hb-actions">
        <button
          type="button"
          className="ev-btn ev-btn-outline ev-btn-sm"
          onClick={autoFill}
          disabled={autoFilling || saving}
        >
          {autoFillLabel}
        </button>
        <button
          type="button"
          className="ev-btn ev-btn-primary ev-btn-sm"
          onClick={save}
          disabled={saving || !dirty}
        >
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </div>
  );
}
