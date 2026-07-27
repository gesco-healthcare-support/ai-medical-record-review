import { act, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { PdfViewer, type PdfViewerHandle } from "@/components/review/pdf-viewer";

/** Stand in for the pdf.js viewer the iframe would boot in a real browser (jsdom loads nothing). */
function stubViewerWindow(iframe: HTMLIFrameElement, app: unknown) {
  Object.defineProperty(iframe, "contentWindow", {
    value: app ? { PDFViewerApplication: app } : {},
    configurable: true,
  });
}

function renderViewer() {
  const ref = createRef<PdfViewerHandle>();
  const { container } = render(<PdfViewer ref={ref} documentId="d1" filename="record.pdf" />);
  return { ref, iframe: container.querySelector("iframe") as HTMLIFrameElement };
}

describe("PdfViewer.jumpTo", () => {
  it("re-applies the page even when the reader scrolled away since the last jump", () => {
    const { ref, iframe } = renderViewer();
    const app = { page: 1, pdfViewer: { pagesCount: 9 } };
    stubViewerWindow(iframe, app);

    act(() => ref.current?.jumpTo(7));
    expect(app.page).toBe(7);
    // The page total only lands via the viewer's own events, so the header shows the page alone.
    expect(screen.getByText(/Page 7/)).toBeInTheDocument();

    app.page = 2; // the reader scrolls off to another page
    act(() => ref.current?.jumpTo(7)); // clicking the same row must bring them back
    expect(app.page).toBe(7);
  });

  it("opens the iframe at the page while the viewer is still booting, but only once", () => {
    const { ref, iframe } = renderViewer();
    stubViewerWindow(iframe, null); // no PDFViewerApplication yet

    act(() => ref.current?.jumpTo(3));
    expect(iframe.src).toContain("#page=3");

    iframe.src = "about:blank#sentinel"; // a second identical jump must not reload the iframe
    act(() => ref.current?.jumpTo(3));
    expect(iframe.src).toBe("about:blank#sentinel");
  });
});
