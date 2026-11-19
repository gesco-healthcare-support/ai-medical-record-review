/** The files a record arrives in, and the order they are put together in (POST
 *  /api/documents/combine). Mirrors `backend/app/services/received_files.py` `KINDS`, which the
 *  server checks every kind against.
 *
 *  The order is the one the office's finished records show (measured on the live box, 2026-11-18):
 *  the office's cover sheet, the internal email the records came with, the advocacy letter, the
 *  declaration, the proof of service, then the medical records with their depositions. */
export const RECEIVED_KINDS = [
  "cover",
  "email",
  "advocacy_letter",
  "declaration",
  "proof_of_service",
  "records",
] as const;

export type ReceivedKind = (typeof RECEIVED_KINDS)[number];

export const RECEIVED_KIND_LABELS: Record<ReceivedKind, string> = {
  cover: "Cover sheet",
  email: "Internal email",
  advocacy_letter: "Advocacy letter",
  declaration: "Declaration",
  proof_of_service: "Proof of service",
  records: "Medical records",
};

// Checked in this order: "declaration of service" is a proof of service, not the declaration.
const GUESSES: ReadonlyArray<[RegExp, ReceivedKind]> = [
  [/\b(proof of service|pos|declaration of service)\b/, "proof_of_service"],
  [/\b(e ?mail|emails|eml|msg|outlook|correspondence)\b/, "email"],
  [/\b(advocacy|adv ?ltr|advltr|cover letter|letter|ltr)\b/, "advocacy_letter"],
  [/\b(declaration|decl?|attest|attestation|custodian|affidavit)\b/, "declaration"],
  [/\b(cover|cover sheet)\b/, "cover"],
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

/** `items` in the order a record is put together: by kind, and within a kind in the order given. */
export function inReceivedOrder<T extends { kind: ReceivedKind }>(items: readonly T[]): T[] {
  const rank = (kind: ReceivedKind) => RECEIVED_KINDS.indexOf(kind);
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => rank(a.item.kind) - rank(b.item.kind) || a.index - b.index)
    .map(({ item }) => item);
}
