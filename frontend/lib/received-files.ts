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

/** The kind to show for a file once the server has read it: what its first page says, unless the
 *  page said nothing more than "records" and the file's name is more specific (a scanned page the
 *  server could not read named "POS.pdf" is still a proof of service). */
export function pickKind(fromPage: ReceivedKind, fromName: ReceivedKind): ReceivedKind {
  return fromPage === "records" ? fromName : fromPage;
}

/** `items` in the order a record is put together: by kind, and within a kind in the order given. */
export function inReceivedOrder<T extends { kind: ReceivedKind }>(items: readonly T[]): T[] {
  const rank = (kind: ReceivedKind) => RECEIVED_KINDS.indexOf(kind);
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => rank(a.item.kind) - rank(b.item.kind) || a.index - b.index)
    .map(({ item }) => item);
}
