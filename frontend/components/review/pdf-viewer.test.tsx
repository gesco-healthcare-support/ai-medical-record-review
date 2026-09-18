import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

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

/** The markup-editor controls pdf.js renders, which this pane hides. Returned so a test can assert
 *  on the elements themselves rather than on a selector that may match nothing. */
function plantMarkupTools(iframe: HTMLIFrameElement) {
  const doc = iframe.contentDocument as Document;
  // jsdom never fetches the iframe's src, so the document it hands back is genuinely empty - no
  // documentElement and no body - unlike the booted viewer this stands in for.
  let host = doc.body;
  if (!host) {
    const html = doc.documentElement ?? doc.appendChild(doc.createElement("html"));
    host = html.appendChild(doc.createElement("body"));
  }
  const made = ["editorModeButtons", "editorModeSeparator"].map((id) => {
    const el = doc.createElement("div");
    el.id = id;
    host.append(el);
    return el;
  });
  // Assert the scaffolding did its job: if these are not reachable by the selector the component
  // uses, the test below would report "not hidden" and read as a defect in the component.
  for (const id of ["#editorModeButtons", "#editorModeSeparator"]) {
    if (!doc.querySelector(id)) throw new Error(`test setup failed: ${id} is not in the document`);
  }
  return { buttons: made[0], separator: made[1] };
}

/** A stand-in pdf.js application whose event bus records its subscribers, so a test can fire the
 *  viewer's own events rather than reaching into the component. */
function viewerApp(page: number, pagesCount: number) {
  const handlers: Record<string, () => void> = {};
  return {
    app: { page, pdfViewer: { pagesCount }, eventBus: { on: (n: string, cb: () => void) => void (handlers[n] = cb) } },
    handlers,
  };
}

describe("PdfViewer chrome", () => {
  it("hides the PDF markup editors once the viewer loads", () => {
    // Highlight / Text / Draw annotate the SOURCE record. They have no place in review, and pdf.js
    // styles them with a higher-specificity rule, so they are hidden inline - which is exactly the
    // kind of thing that stops working silently when the vendored viewer is updated.
    const { iframe } = renderViewer();
    const { buttons, separator } = plantMarkupTools(iframe);
    stubViewerWindow(iframe, null);

    fireEvent.load(iframe);

    expect(buttons.style.display).toBe("none");
    expect(separator.style.display).toBe("none");
  });

  it("shows the page the viewer reports, and follows it as the reader scrolls", () => {
    const { iframe } = renderViewer();
    const { app, handlers } = viewerApp(3, 12);
    stubViewerWindow(iframe, app);

    fireEvent.load(iframe);
    expect(screen.getByText(/Page 3 of 12/)).toBeInTheDocument();

    app.page = 9;
    act(() => handlers.pagechanging());
    expect(screen.getByText(/Page 9 of 12/)).toBeInTheDocument();
  });
});

describe("PdfViewer fallback poll", () => {
  it("trims and syncs without a load event, for builds that fire their events too early", () => {
    // Some pdf.js builds emit pagesloaded before our listener attaches, so onLoad alone would leave
    // the header stuck at "Page 1" and the markup editors on screen. The poll is the only thing
    // covering that, and it is invisible until it is gone.
    vi.useFakeTimers();
    try {
      const { iframe } = renderViewer();
      const { buttons } = plantMarkupTools(iframe);
      const { app } = viewerApp(5, 20);
      stubViewerWindow(iframe, app);

      // Deliberately NO load event.
      act(() => void vi.advanceTimersByTime(1000));

      expect(buttons.style.display).toBe("none");
      expect(screen.getByText(/Page 5 of 20/)).toBeInTheDocument();
    } finally {
      // Restored in-test: a fake clock leaking into the rest of this suite would surface as the
      // recorded use-review-workflow debounce flake and be blamed on it.
      vi.useRealTimers();
    }
  });
});
