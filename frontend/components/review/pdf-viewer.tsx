"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

export type PdfViewerHandle = { jumpTo: (page: number) => void };

type PdfViewerApp = {
  page: number;
  pdfViewer?: { pagesCount: number };
  eventBus?: { on: (name: string, cb: () => void) => void };
};

// Keep the pdf.js viewer's genuinely useful tools (zoom, find, print, download, page nav, sidebar) -
// reviewers rely on them while reading records. Hide ONLY the PDF markup EDITORS (Highlight / Text /
// Draw / Add images), which annotate the source PDF and have no place in review. pdf.js styles these
// with a higher-specificity rule, so an injected stylesheet loses; setting display:none INLINE (with
// !important) on each element wins. Applied on load + a few polls (pdf.js renders its UI async).
const MARKUP_SELECTORS = ["#editorModeButtons", "#editorModeSeparator"];

function trimMarkupTools(doc: Document) {
  for (const sel of MARKUP_SELECTORS) {
    (doc.querySelector(sel) as HTMLElement | null)?.style.setProperty("display", "none", "important");
  }
}

/**
 * PDF pane: the vendored pdf.js viewer (public/pdfjs) in an iframe, with its default UI hidden so
 * only the pages show in a dark well under a slim "Page N of M" header. Row-click navigation calls
 * the viewer's programmatic page API (Chrome's native viewer ignores #page after first load).
 * Same-origin: the viewer fetches /api/documents/{id}/pdf with the session cookie, and lets us
 * inject the chrome-hiding CSS + read the current page for the header.
 */
export const PdfViewer = forwardRef<PdfViewerHandle, { documentId: string; filename?: string }>(
  function PdfViewer({ documentId, filename }, ref) {
    const frameRef = useRef<HTMLIFrameElement>(null);
    const lastPage = useRef(1);
    const [pageInfo, setPageInfo] = useState({ page: 1, total: 0 });
    const file = encodeURIComponent(`/api/documents/${documentId}/pdf`);
    const srcFor = (page: number) => `/pdfjs/web/viewer.html?file=${file}#page=${page}`;

    function viewerApp(): PdfViewerApp | null {
      try {
        const win = frameRef.current?.contentWindow as unknown as {
          PDFViewerApplication?: PdfViewerApp;
        } | null;
        return win?.PDFViewerApplication ?? null;
      } catch {
        return null; // still booting
      }
    }

    // Trim the markup editors + start tracking the current page once the viewer boots.
    function onLoad() {
      const doc = frameRef.current?.contentDocument;
      if (doc) trimMarkupTools(doc);
      const app = viewerApp();
      const sync = () => {
        const a = viewerApp();
        if (a?.pdfViewer?.pagesCount) {
          setPageInfo({ page: a.page || 1, total: a.pdfViewer.pagesCount });
        }
      };
      app?.eventBus?.on("pagechanging", sync);
      app?.eventBus?.on("pagesloaded", sync);
      sync();
    }

    useImperativeHandle(
      ref,
      () => ({
        jumpTo(page: number) {
          if (page === lastPage.current) return;
          lastPage.current = page;
          const app = viewerApp();
          if (app?.pdfViewer?.pagesCount) {
            app.page = page;
            setPageInfo((p) => ({ ...p, page }));
            return;
          }
          const frame = frameRef.current;
          if (frame) frame.src = srcFor(page); // viewer not ready yet: (re)load opened at the page
        },
      }),
      [file],
    );

    // Fallback poll: some pdf.js builds fire events before our listener attaches on first load;
    // also re-trim the markup editors in case the viewer re-rendered them after our onLoad pass.
    useEffect(() => {
      const id = setInterval(() => {
        const doc = frameRef.current?.contentDocument;
        if (doc) trimMarkupTools(doc);
        const a = viewerApp();
        if (a?.pdfViewer?.pagesCount) {
          setPageInfo((prev) => {
            const next = { page: a.page || prev.page, total: a.pdfViewer!.pagesCount };
            return next.page === prev.page && next.total === prev.total ? prev : next;
          });
        }
      }, 1000);
      return () => clearInterval(id);
    }, []);

    return (
      <div className="pdf-pane">
        <div className="pdf-pane-head">
          <span className="pdf-pane-name">{filename || "Document"}</span>
          <span className="pdf-pane-page">
            Page {pageInfo.page}
            {pageInfo.total ? ` of ${pageInfo.total}` : ""}
          </span>
        </div>
        <iframe id="pdfFrame" ref={frameRef} title="PDF viewer" src={srcFor(1)} onLoad={onLoad} />
      </div>
    );
  },
);
