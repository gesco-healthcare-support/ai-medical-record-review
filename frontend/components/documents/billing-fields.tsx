"use client";

import {
  type BillingDraft,
  isPageCount,
  takesAppointment,
  VISIT_TYPES,
  visitLabel,
} from "@/lib/billing-sheet";

/** The billing sheet's fields (`lib/billing-sheet.ts`): what the office's sheet needs that the
 *  files cannot say. Shown in the received-files upload, where the type of visit and the
 *  interpreter are required, and again on the billing sheet dialog. Who reviews the records is not
 *  asked: the office reviews every record in house, and the sheet always says IN-HOUSE. A
 *  supplemental report and late meds have no appointment, so the date box goes away - the sheet's
 *  date of service says SUPP or LATE MEDS.
 *
 *  `idPrefix` keeps the two forms' ids apart. A doctor already on the record that is not on
 *  today's list stays offered, so opening the sheet never blanks it. */
export function BillingFields({
  value,
  onChange,
  doctors,
  idPrefix,
}: Readonly<{
  value: BillingDraft;
  onChange: (patch: Partial<BillingDraft>) => void;
  doctors: readonly string[];
  idPrefix: string;
}>) {
  const id = (name: string) => `${idPrefix}-${name}`;
  const withAppointment = takesAppointment(value.visit_type);
  const doctorOptions =
    value.doctor && !doctors.includes(value.doctor) ? [...doctors, value.doctor] : doctors;
  return (
    <>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("visit")}>
          Type of visit
        </label>
        <select
          id={id("visit")}
          className="ev-inp"
          value={value.visit_type}
          onChange={(e) => onChange({ visit_type: e.target.value })}
        >
          <option value="">Choose...</option>
          {VISIT_TYPES.map((visit) => (
            <option key={visit} value={visit}>
              {visitLabel(visit)}
            </option>
          ))}
        </select>
      </div>
      {withAppointment ? (
        <div className="grid gap-1.5">
          <label className="ev-lbl" htmlFor={id("appointment")}>
            Appointment date
          </label>
          <input
            id={id("appointment")}
            type="date"
            className="ev-inp"
            value={value.appointment_date}
            onChange={(e) => onChange({ appointment_date: e.target.value })}
          />
        </div>
      ) : null}
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("doctor")}>
          Doctor
        </label>
        <select
          id={id("doctor")}
          className="ev-inp"
          value={value.doctor}
          onChange={(e) => onChange({ doctor: e.target.value })}
        >
          <option value="">Not chosen</option>
          {doctorOptions.map((doctor) => (
            <option key={doctor} value={doctor}>
              {doctor}
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("interpreter")}>
          Interpreter
        </label>
        <select
          id={id("interpreter")}
          className="ev-inp"
          value={value.interpreter}
          onChange={(e) => onChange({ interpreter: e.target.value })}
        >
          <option value="">Choose...</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </div>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("claims")}>
          Claim numbers
        </label>
        <input
          id={id("claims")}
          className="ev-inp"
          autoComplete="off"
          placeholder="Separate several with commas"
          value={value.claim_numbers}
          onChange={(e) => onChange({ claim_numbers: e.target.value })}
        />
      </div>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("location")}>
          Location
        </label>
        <input
          id={id("location")}
          className="ev-inp"
          autoComplete="off"
          placeholder="Where the evaluation takes place"
          value={value.location}
          onChange={(e) => onChange({ location: e.target.value })}
        />
      </div>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("previous")}>
          Previous office visit
        </label>
        <input
          id={id("previous")}
          type="date"
          className="ev-inp"
          value={value.previous_visit}
          onChange={(e) => onChange({ previous_visit: e.target.value })}
        />
      </div>
      <div className="grid gap-1.5">
        <label className="ev-lbl" htmlFor={id("declared")}>
          Pages on the declaration
        </label>
        <input
          id={id("declared")}
          className="ev-inp"
          inputMode="numeric"
          autoComplete="off"
          placeholder="Blank when there is none"
          value={value.declared_pages}
          onChange={(e) => onChange({ declared_pages: e.target.value })}
        />
        {isPageCount(value.declared_pages) ? null : (
          <output className="rc-hb-warn">Enter the page count as a whole number.</output>
        )}
      </div>
    </>
  );
}
