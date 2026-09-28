/** Category-bundle downloads (Diagnostic & Operative / Depositions). Both end in a file, so they
 *  go through `downloadFile` (not the JSON apiFetch), which hands it to the browser to download. */

import { downloadFile } from "@/lib/download";

export type BundleConfig = {
  label: string;
  slug: string;
  categories: string[];
  // The heading of the list page that goes in front of the combined PDF. The reviewers
  // asked for one on Diagnostics and sent their own as the example; depositions has none,
  // so that bundle sends no heading and gets no cover page.
  coverHeading?: string;
  // What the downloaded file calls itself, AFTER the patient name the backend prepends:
  // `Lastname_Firstname_Medical_Records_<downloadName>.pdf`. The reviewers asked for the
  // diagnostic download to carry the patient name "similar to how the other files are named" -
  // it was the one deliverable named only for its own category, so in a folder of four files it
  // was the only one that did not say whose record it was.
  //
  // DECLARED per bundle rather than derived from `coverHeading` or `slug`: it is the name a
  // client reads, "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS" would need title-casing rules that
  // guess at connectives, and `depositions` has no heading to derive from at all.
  downloadName: string;
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
  // The reviewers named this string themselves, so it is theirs rather than a shortening of
  // the cover heading.
  downloadName: "List of Diagnostic and Operative Reports",
};

export const DEPOSITIONS: BundleConfig = {
  label: "Depositions",
  slug: "depositions",
  categories: ["9"],
  downloadName: "Depositions",
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
    {
      categories: config.categories,
      label: config.slug,
      coverHeading: config.coverHeading,
      downloadName: config.downloadName,
    },
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
    {
      categories: config.categories,
      label: config.slug,
      downloadName: config.downloadName,
      ...fields,
    },
    `${config.slug}.docx`,
  );
}
