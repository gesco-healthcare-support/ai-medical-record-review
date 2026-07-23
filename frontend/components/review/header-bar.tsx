"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { humanizeError } from "@/lib/errors";
import { extractHeader, saveHeader, type HeaderFields } from "@/lib/review-api";

const EMPTY: HeaderFields = {
  patient_first_name: "",
  patient_last_name: "",
  patient_dob: "",
  law_firm: "",
};

/** Editable report-header bar shown on Review & correct and Summaries: patient first/last name, DOB,
 *  and law firm. Auto-fill / Re-detect extracts from the record AND persists it in one action (no
 *  separate Save needed); manual edits still save explicitly via PUT /header. Persisted values feed
 *  the export filename + header and are shared across pages through the parent's header state. */
export function HeaderBar({
  documentId,
  header,
  onSaved,
}: {
  documentId: string;
  header: HeaderFields | null;
  onSaved: (fields: HeaderFields) => void;
}) {
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
      const data = await extractHeader(documentId);
      // extractHeader now persists server-side; reflect it as the shared saved header (no Save step).
      setFields(data);
      setDirty(false);
      onSaved(data);
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
        <label className="rc-hb-field rc-hb-firm">
          <span className="ev-lbl">Attorney / law firm</span>
          <input
            className="ev-inp"
            value={fields.law_firm}
            onChange={(e) => set("law_firm", e.target.value)}
            placeholder="Firm name"
          />
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
