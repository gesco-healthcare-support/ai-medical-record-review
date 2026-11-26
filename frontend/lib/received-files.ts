/** The files a record arrives in, and the order they are put together in (POST
 *  /api/documents/combine). Mirrors `backend/app/services/received_files.py` `KINDS`, which the
 *  server checks every kind against.
 *
 *  The order is the office's own guideline (2026-11-18, confirmed 2026-11-19): advocacy letter,
 *  declaration, medical index (the sender's list of the records in the file), medical records with
 *  their depositions, proof of service, internal email; an envelope last. The office's cover sheet
 *  is no longer used. */
export const RECEIVED_KINDS = [
  "advocacy_letter",
  "declaration",
  "medical_index",
  "records",
  "proof_of_service",
  "email",
  "envelope",
] as const;

export type ReceivedKind = (typeof RECEIVED_KINDS)[number];

export const RECEIVED_KIND_LABELS: Record<ReceivedKind, string> = {
  advocacy_letter: "Advocacy letter",
  declaration: "Declaration",
  medical_index: "Medical index",
  records: "Medical records",
  proof_of_service: "Proof of service",
  email: "Internal email",
  envelope: "Envelope",
};

/** Who sent the records: the four rows of the office's billing sheet, one party per record. Mirrors
 *  `received_files.PARTIES`; the uploader picks it, as the letter does not always say. */
export const RECORDS_FROM = ["defense", "applicant", "insurance", "copy_service"] as const;

export type RecordsFrom = (typeof RECORDS_FROM)[number];

export const RECORDS_FROM_LABELS: Record<RecordsFrom, string> = {
  defense: "Defense",
  applicant: "Applicant",
  insurance: "Insurance",
  copy_service: "Copy service",
};

// Checked in this order: "declaration of service" is a proof of service, not the declaration.
const GUESSES: ReadonlyArray<[RegExp, ReceivedKind]> = [
  [/\b(proof of service|pos|declaration of service)\b/, "proof_of_service"],
  [/\b(e ?mail|emails|eml|msg|outlook|correspondence)\b/, "email"],
  [/\b(advocacy|adv ?ltr|advltr|cover letter|letter|ltr)\b/, "advocacy_letter"],
  [/\b(declaration|decl?|attest|attestation|custodian|affidavit)\b/, "declaration"],
  [/\b(index|med index|medical index)\b/, "medical_index"],
  [/\b(envelope|envelopes|env)\b/, "envelope"],
];

/** A first guess at what a file is, from its name; the uploader corrects it. Anything the name does
 *  not say is a medical record - most files are. */
export function guessKind(fileName: string): ReceivedKind {
  const words = fileName
    .toLowerCase()
    .replace(/\.[a-z0-9]{2,4}$/, "")
    .replace(/[^a-z0-9]+/g, " ");
  for (const [pattern, kind] of GUESSES) {
    if (pattern.test(words)) return kind;
  }
  return "records";
}

/** What the server said a file is, from its first page: the model's reading, and whether the
 *  uploader should look (the model unsure, or off and the keyword rules guessing). */
export type FileReading = { kind: ReceivedKind; check: boolean };

/** The kind to show once the server has read a file, and whether the uploader must look first.
 *  The server's reading wins - the name is often wrong. The name is used only when the reading was
 *  unsure and said no more than "records" (a scan the server could not read, named "POS.pdf", is
 *  still a proof of service). A name that names another kind than the reading also asks for a
 *  look. */
export function shownKind(reading: FileReading, fromName: ReceivedKind): FileReading {
  const kind =
    reading.check && reading.kind === "records" && fromName !== "records" ? fromName : reading.kind;
  return { kind, check: reading.check || (fromName !== "records" && fromName !== kind) };
}

/** The files a record can be put together from: PDFs, and scans sent as images, which the server
 *  turns into PDF pages. Judged by type or by name, as browsers leave the type empty for some. */
const ACCEPTED_TYPES = new Set([
  "application/pdf",
  "image/jpeg",
  "image/png",
  "image/tiff",
  "image/gif",
  "image/bmp",
]);
const ACCEPTED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp"];
const ACCEPTED_NAME = /\.(pdf|jpe?g|png|tiff?|gif|bmp)$/i;

/** The file input's `accept`: every type and extension above. */
export const ACCEPTED_FILES = [...ACCEPTED_TYPES, ...ACCEPTED_EXTENSIONS].join(",");

export function isAcceptedFile(file: File): boolean {
  return ACCEPTED_TYPES.has(file.type) || ACCEPTED_NAME.test(file.name);
}

/** A record that already holds a file being added, as the server describes it when the file is
 *  sent (`uploaded_before`, newest first, at most five): when it was uploaded, its pages, where it
 *  is now, and whether the file is only one of several the record was put together from. Uploads
 *  are the whole office's (the team queue), so the server looks in every account. */
export type UploadedBefore = {
  uploaded_at: string;
  pages: number;
  in_team_queue: boolean;
  yours: boolean;
  /** The reviewer who has it, when it is neither in the team queue nor the uploader's. */
  reviewer: string;
  part_of_record: boolean;
};

/** The server says at most this many earlier records (`_EARLIER_UPLOADS_SHOWN`). */
const EARLIER_SHOWN = 5;

/** `MM/DD/YYYY` of a timestamp, in the viewer's own time zone. */
function localDay(iso: string): string {
  const day = new Date(iso);
  const two = (value: number) => String(value).padStart(2, "0");
  return `${two(day.getMonth() + 1)}/${two(day.getDate())}/${day.getFullYear()}`;
}

function whereItIs(earlier: UploadedBefore): string {
  if (earlier.in_team_queue) return "in the team queue";
  if (earlier.yours) return "in your records";
  return earlier.reviewer ? `in ${earlier.reviewer}'s records` : "in another reviewer's records";
}

/** The warning on a file the office already has, or "" for a new file: when, where and how big the
 *  latest record holding it is, and how often the file was uploaded before. */
export function uploadedBeforeLine(earlier: readonly UploadedBefore[]): string {
  const latest = earlier[0];
  if (latest === undefined) return "";
  const pages = latest.pages === 1 ? "1 page" : `${latest.pages.toLocaleString("en-US")} pages`;
  const part = latest.part_of_record ? " as part of a record" : "";
  const where = whereItIs(latest);
  const line = `Already uploaded on ${localDay(latest.uploaded_at)}${part}, ${where} (${pages}).`;
  if (earlier.length === 1) return line;
  const times = earlier.length >= EARLIER_SHOWN ? `${EARLIER_SHOWN} or more` : `${earlier.length}`;
  return `${line} Uploaded ${times} times before; that is the latest.`;
}

/** `items` in the order a record is put together: by kind, and within a kind in the order given. */
export function inReceivedOrder<T extends { kind: ReceivedKind }>(items: readonly T[]): T[] {
  const rank = (kind: ReceivedKind) => RECEIVED_KINDS.indexOf(kind);
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => rank(a.item.kind) - rank(b.item.kind) || a.index - b.index)
    .map(({ item }) => item);
}
