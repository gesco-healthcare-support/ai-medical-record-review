/** Category-bundle downloads (Diagnostic & Operative / Depositions). Both stream a file, so they
 *  go through `downloadFile` (not the JSON apiFetch) to read the blob + Content-Disposition. */

import { downloadFile } from "@/lib/download";

export type BundleConfig = { label: string; slug: string; categories: string[] };

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
    { categories: config.categories, label: config.slug },
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
