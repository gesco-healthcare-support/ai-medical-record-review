/** Category-bundle downloads (Diagnostic & Operative / Depositions). Both stream a file, so they
 *  go through fetch directly (not the JSON apiFetch) to read the blob + Content-Disposition. */

export type BundleConfig = { label: string; slug: string; categories: string[] };

export type BundleHeaderFields = {
  patientName: string;
  patientdob: string;
  QMEorAME: string;
  lawfirm: string;
};

async function downloadBundle(
  documentId: string,
  action: "pdf" | "summarize",
  body: Record<string, unknown>,
  fallbackName: string,
) {
  const resp = await fetch(`/api/documents/${documentId}/bundle/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (resp.status === 401) {
    window.location.assign("/login");
    return;
  }
  if (!resp.ok) {
    let detail = `request failed (${resp.status})`;
    try {
      const data = await resp.json();
      detail = data.detail || data.error || detail;
    } catch {
      // non-JSON error body; keep the status-code fallback
    }
    throw new Error(detail);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? match[1] : fallbackName;
  link.click();
  URL.revokeObjectURL(url);
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
