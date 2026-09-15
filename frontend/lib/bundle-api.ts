/** Category-bundle downloads (Diagnostic & Operative / Depositions). Both stream a file, so they
 *  go through `downloadFile` (not the JSON apiFetch) to read the blob + Content-Disposition. */

import { downloadFile } from "@/lib/download";

export type BundleConfig = {
  label: string;
  slug: string;
  categories: string[];
  // The heading of the list page that goes in front of the combined PDF. The reviewers
  // asked for one on Diagnostics and sent their own as the example; depositions has none,
  // so that bundle sends no heading and gets no cover page.
  coverHeading?: string;
};

/** The two bundles the app offers, defined ONCE.
 *
 *  Each page used to inline its own copy, and the export zip would have made a third - so the
 *  category lists would have had to be kept in step by hand across three files. The backend has
 *  no copy at all: /export/zip is handed these, which is why the taxonomy lives on this side. */
export const DIAGNOSTIC_OPERATIVE: BundleConfig = {
  label: "Diagnostic & Operative",
  slug: "diagnostic-operative",
  categories: ["3", "8"],
  coverHeading: "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS",
};

export const DEPOSITIONS: BundleConfig = {
  label: "Depositions",
  slug: "depositions",
  categories: ["9"],
};

export const BUNDLES: BundleConfig[] = [DIAGNOSTIC_OPERATIVE, DEPOSITIONS];

export type BundleHeaderFields = {
  patientName: string;
  patientdob: string;
  QMEorAME: string;
  lawfirm: string;
};

function downloadBundle(
  documentId: string,
  action: "pdf" | "summarize",
  body: Record<string, unknown>,
  fallbackName: string,
) {
  return downloadFile(`/documents/${documentId}/bundle/${action}`, body, fallbackName);
}

/** Concatenate the category-matched documents' pages into one PDF (no LLM). */
export function downloadBundlePdf(documentId: string, config: BundleConfig) {
  return downloadBundle(
    documentId,
    "pdf",
    { categories: config.categories, label: config.slug, coverHeading: config.coverHeading },
    `${config.slug}.pdf`,
  );
}

/** Summarize just the category-matched documents into a filtered Word report. */
export function downloadBundleSummary(
  documentId: string,
  config: BundleConfig,
  fields: BundleHeaderFields,
) {
  return downloadBundle(
    documentId,
    "summarize",
    { categories: config.categories, label: config.slug, ...fields },
    `${config.slug}.docx`,
  );
}
