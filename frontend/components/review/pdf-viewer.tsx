"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";

export type PdfViewerHandle = { jumpTo: (page: number) => void };

type PdfViewerApp = { page: number; pdfViewer?: { pagesCount: number } };

/**
 * PDF pane: the vendored pdf.js viewer in an iframe (public/pdfjs). Chrome's native viewer
 * ignores #page after first load, so row-click navigation calls the viewer's programmatic page
 * API (PDFViewerApplication.page). Same-origin: the viewer fetches /api/documents/{id}/pdf with
 * the session cookie.
 */
export const PdfViewer = forwardRef<PdfViewerHandle, { documentId: string }>(function PdfViewer(
  { documentId },
  ref,
) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const lastPage = useRef(1);
  const file = encodeURIComponent(`/api/documents/${documentId}/pdf`);
  const srcFor = (page: number) => `/pdfjs/web/viewer.html?file=${file}#page=${page}`;

  useImperativeHandle(
    ref,
    () => ({
      jumpTo(page: number) {
        if (page === lastPage.current) return;
        lastPage.current = page;
        const frame = frameRef.current;
        if (!frame) return;
        try {
          const win = frame.contentWindow as unknown as {
            PDFViewerApplication?: PdfViewerApp;
          } | null;
          const viewer = win?.PDFViewerApplication;
          if (viewer?.pdfViewer?.pagesCount) {
            viewer.page = page;
            return;
          }
        } catch {
          // Same-origin, so this only happens while the viewer is still booting.
        }
        frame.src = srcFor(page); // viewer not ready yet: (re)load opened at the page
      },
    }),
    [file],
  );

  return <iframe id="pdfFrame" ref={frameRef} title="PDF viewer" src={srcFor(1)} />;
});
