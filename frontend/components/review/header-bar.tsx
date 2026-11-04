"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check } from "lucide-react";
import { toast } from "sonner";
import { humanizeError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { extractHeader, saveHeader, type HeaderFields } from "@/lib/review-api";

/** How long after the last keystroke the header saves itself - the rows' autosave delay. */
export const HEADER_SAVE_DELAY_MS = 800;

/** The autosave line beside the buttons: waiting, saving, saved, or why it did not save. */
type HeaderSave = { kind: "" | "dirty" | "saving" | "saved" | "error"; message?: string };

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

/** Where the Save button was: what the autosave is doing, in the rows' chip style, and a Retry
 *  when a save failed. Nothing until the reviewer edits. */
function HeaderSaveStatus({
  status,
  onRetry,
}: Readonly<{ status: HeaderSave; onRetry: () => void }>) {
  if (!status.kind) return null;
  const tone = status.kind === "saving" ? "dirty" : status.kind;
  return (
    <>
      <span className={cn("rc-save", tone)}>
        {status.kind === "saved" ? (
          <>
            <Check width={14} height={14} aria-hidden /> Saved
          </>
        ) : (
          status.message
        )}
      </span>
      {status.kind === "error" ? (
        <button type="button" className="ev-btn ev-btn-outline ev-btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </>
  );
}

/** Editable report-header bar shown on Review & correct and Summaries: patient first/last name, DOB,
 *  and law firm. Auto-fill / Re-detect extracts from the record AND persists it in one action.
 *  A reviewer's edits SAVE THEMSELVES (PUT /header), like the rows: HEADER_SAVE_DELAY_MS after the
 *  last keystroke, at once when a field loses focus or a dropdown changes, and on leaving the page.
 *  A reviewer asked for it on 2026-11-04: people forgot the Save button because everything else on
 *  the page saves by itself. Saves run one at a time, in order, each sending the whole header, so
 *  the last edit is the one stored. Persisted values feed the export filename + header and are
 *  shared across pages through the parent's header state. */
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
  // An edit not yet stored. A ref, read by the re-seed below, because nothing renders from it.
  const dirtyRef = useRef(false);
  const [autoFilling, setAutoFilling] = useState(false);
  const [status, setStatus] = useState<HeaderSave>({ kind: "" });
  // The latest fields, for the async save and the unmount flush, which run outside a render.
  const fieldsRef = useRef<HeaderFields>(fields);
  // The header a debounced save is holding, or null once it has gone out.
  const pendingRef = useRef<HeaderFields | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Every save waits for the one before it, so an older header can never land after a newer one.
  const chainRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const onSavedRef = useRef(onSaved);
  useEffect(() => {
    onSavedRef.current = onSaved;
  }, [onSaved]);

  // Re-seed when the persisted header changes (e.g. after identify) unless the user is mid-edit.
  // On the header changing only, never on an edit settling: a save finishing is not news about the
  // record, and re-reading then would put an older header back over what was just typed.
  useEffect(() => {
    if (dirtyRef.current) return;
    fieldsRef.current = header ?? EMPTY;
    setFields(fieldsRef.current);
  }, [header]);

  /** Send ``snapshot`` after any save already on its way. True when it was stored. */
  function send(docId: string, snapshot: HeaderFields): Promise<boolean> {
    setStatus({ kind: "saving", message: "Saving..." });
    const run = chainRef.current.then(async () => {
      try {
        await saveHeader(docId, snapshot);
        onSavedRef.current(snapshot);
        // Only when nothing was typed while it was on its way; otherwise the next save follows.
        if (fieldsRef.current === snapshot) {
          dirtyRef.current = false;
          setStatus({ kind: "saved" });
        }
        return true;
      } catch (err) {
        setStatus({
          kind: "error",
          message: `Not saved: ${humanizeError(err, { fallback: "please try again" })}`,
        });
        return false;
      }
    });
    chainRef.current = run;
    return run;
  }

  /** Send the header a debounced save is holding NOW; resolves once every save so far is done. */
  function flush(docId: string = documentId): Promise<boolean> {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    const pending = pendingRef.current;
    pendingRef.current = null;
    return pending ? send(docId, pending) : chainRef.current;
  }

  // Leaving the page, or this bar moving to another record, sends what is still waiting - to the
  // record it was typed for. "Unsaved changes..." is a promise to save.
  useEffect(
    () => () => {
      void flush(documentId);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [documentId],
  );

  function set(key: keyof HeaderFields, value: string, { now = false } = {}) {
    const next = { ...fieldsRef.current, [key]: value };
    fieldsRef.current = next;
    setFields(next);
    dirtyRef.current = true;
    pendingRef.current = next;
    setStatus({ kind: "dirty", message: "Unsaved changes..." });
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = now ? null : setTimeout(() => void flush(), HEADER_SAVE_DELAY_MS);
    if (now) void flush();
  }

  async function autoFill() {
    setAutoFilling(true);
    try {
      // A typed edit lands BEFORE the detection, never after it: arriving later, it would put the
      // values the detection just replaced back. A save that failed stops here, with its message.
      if (!(await flush())) return;
      const detected = await extractHeader(documentId);
      // extractHeader persists server-side; reflect it as the shared saved header (no Save step).
      // The reply holds only the detected fields (the four, plus any covering-letter field the
      // server pre-fills), so it is merged over the STORED header: the rest are whatever the server
      // still has. Replacing the header with the reply left them undefined, and the next save then
      // stored each of them as "".
      const saved = { ...fieldsRef.current, ...detected };
      fieldsRef.current = saved;
      setFields(saved);
      dirtyRef.current = false;
      setStatus({ kind: "" });
      onSaved(saved);
      toast.success("Header detected and saved.");
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not read the header." }));
    } finally {
      setAutoFilling(false);
    }
  }

  function retry() {
    pendingRef.current = fieldsRef.current;
    void flush();
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
      <div className="rc-hb-fields" onBlur={() => void flush()}>
        <label className="rc-hb-field">
          <span className="ev-lbl">First name</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
            value={fields.patient_first_name}
            onChange={(e) => set("patient_first_name", e.target.value)}
            placeholder="First"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Last name</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
            value={fields.patient_last_name}
            onChange={(e) => set("patient_last_name", e.target.value)}
            placeholder="Last"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">DOB</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
            value={fields.patient_dob}
            onChange={(e) => set("patient_dob", e.target.value)}
            placeholder="MM/DD/YYYY"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Attorney</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
            value={fields.attorney_name}
            onChange={(e) => set("attorney_name", e.target.value)}
            placeholder="Person who sent them"
          />
        </label>
        <label className="rc-hb-field rc-hb-firm">
          <span className="ev-lbl">Law firm</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
            value={fields.law_firm}
            onChange={(e) => set("law_firm", e.target.value)}
            placeholder="Firm name"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Doctor</span>
          <select
            className="ev-inp"
            disabled={autoFilling}
            value={fields.doctor}
            onChange={(e) => set("doctor", e.target.value, { now: true })}
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
            disabled={autoFilling}
            value={fields.letter_type}
            onChange={(e) => set("letter_type", e.target.value, { now: true })}
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
            disabled={autoFilling}
            value={fields.letter_date}
            onChange={(e) => set("letter_date", e.target.value)}
            placeholder="MM/DD/YYYY"
          />
        </label>
        <label className="rc-hb-field">
          <span className="ev-lbl">Pages received</span>
          <input
            className="ev-inp"
            disabled={autoFilling}
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
          disabled={autoFilling}
        >
          {autoFillLabel}
        </button>
        <HeaderSaveStatus status={status} onRetry={retry} />
      </div>
    </div>
  );
}
