/** The patient's ID in an upload's file name - the same rule as the backend's
 *  `app/services/patient_ids.py` (`patient-id.test.ts` holds the two to the same cases).
 *
 *  The office names a record file after the patient: the name, the patient's account number (the
 *  ID) and the date the file was made, in no fixed order. The upload asks for the ID when this
 *  finds none, or two, in the name - it is never guessed. The server reads the name again for an
 *  upload that does not send one (a script's), so this copy only decides whether to ask. */

/** Longest ID the server stores. */
export const PATIENT_ID_MAX_LENGTH = 32;

// The name is read as runs of letters and digits; any other character separates them.
const TOKEN = /[A-Za-z0-9]+/g;
// The four shapes the IDs take: six digits starting 80, seven digits, one letter and five digits,
// two letters and four digits. A year, the eight-digit file date and every piece of a date written
// with separators are too short or too long to match.
const ID_SHAPE = /^(?:[A-Za-z]{1,2}\d{4,5}|\d{6,7})$/;
const MMDDYY = /^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d\d$/;

function candidates(name: string): string[] {
  const stem = name.trim().replace(/\.[A-Za-z0-9]{2,4}$/, "");
  const found: string[] = [];
  for (const token of stem.match(TOKEN) ?? []) {
    if (!ID_SHAPE.test(token)) continue;
    // Six digits that read as a date are the file date. An ID starting 80 never does.
    if (/^\d{6}$/.test(token) && MMDDYY.test(token)) continue;
    const id = token.toUpperCase();
    if (!found.includes(id)) found.push(id);
  }
  return found;
}

/** The one patient ID in `name`, or null when it has none or more than one. */
export function patientIdFromFilename(name: string | null | undefined): string | null {
  const found = candidates(name ?? "");
  return found.length === 1 ? found[0] : null;
}

/** The ID the names that carry one all agree on, or null. For a record combined from pieces. */
export function agreedPatientId(names: readonly string[]): string | null {
  const ids = new Set(
    names.map((n) => patientIdFromFilename(n)).filter((id): id is string => id !== null),
  );
  return ids.size === 1 ? [...ids][0] : null;
}

/** "This patient has N other record(s) in the system." for a count above zero, else "". */
export function otherRecordsLine(count: number | null | undefined): string {
  if (!count) return "";
  return count === 1
    ? "This patient has 1 other record in the system."
    : `This patient has ${count} other records in the system.`;
}

/** A typed ID as the server stores it: letters and digits only, upper-case. */
export function cleanPatientId(value: string): string {
  return value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}
