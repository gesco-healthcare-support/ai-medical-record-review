/**
 * The resizable divider between the sub-documents table and the PDF pane.
 *
 * Both ways of moving it are covered because they are not the same code path: the keyboard nudge is
 * the accessible route and clamps arithmetically, while the drag converts a pointer position against
 * the element's own box. The saved width is what the reviewer gets back on their next record, so a
 * resize that works and never persists looks like the setting being ignored.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SplitPane } from "@/components/review/split-pane";

function renderPane(storageKey = "mrr.test.split") {
  const { container } = render(
    <SplitPane storageKey={storageKey} left={<p>left pane</p>} right={<p>right pane</p>} />,
  );
  return {
    container,
    root: container.querySelector(".ev-split") as HTMLElement,
    handle: screen.getByRole("separator", { name: "Resize panels" }),
  };
}

/** jsdom returns an all-zero DOMRect, and `width` is the DIVISOR in the drag arithmetic.
 *
 *  Left unstubbed the failure is NOT uniform, and the dangerous half is the quiet one. Dividing a
 *  non-zero offset by zero gives `Infinity`, and `Math.min(70, Math.max(24, Infinity))` is **70** -
 *  a clean, plausible number that an assertion on the MAXIMUM bound would happily accept from a
 *  completely broken calculation. Only `clientX === 0` yields `NaN`, and there the assertion fails,
 *  which is the safe outcome.
 *
 *  So the rule is: stub the rect, and assert an INTERIOR value. A bound cannot tell the two apart. */
function giveRootAWidth(root: HTMLElement, width = 1000) {
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    left: 0,
    width,
    top: 0,
    right: width,
    bottom: 0,
    height: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

describe("SplitPane keyboard", () => {
  it("nudges the divider with the arrow keys and remembers the new width", () => {
    const { handle } = renderPane("mrr.kb.split");
    expect(handle).toHaveAttribute("aria-valuenow", "58"); // the default

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(handle).toHaveAttribute("aria-valuenow", "56");
    expect(localStorage.getItem("mrr.kb.split")).toBe("56");

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle).toHaveAttribute("aria-valuenow", "60");
    // Persisted on every nudge, not only on blur: the reviewer never "commits" a keyboard resize.
    expect(localStorage.getItem("mrr.kb.split")).toBe("60");
  });

  it("ignores keys that are not the arrows", () => {
    const { handle } = renderPane("mrr.kb2.split");
    fireEvent.keyDown(handle, { key: "Enter" });
    expect(handle).toHaveAttribute("aria-valuenow", "58");
    expect(localStorage.getItem("mrr.kb2.split")).toBeNull();
  });
});

describe("SplitPane drag", () => {
  it("resizes the panes while the handle is dragged, and saves on release", () => {
    const { root, handle } = renderPane("mrr.drag.split");
    giveRootAWidth(root);

    fireEvent.pointerDown(handle);
    fireEvent.pointerMove(window, { clientX: 400 });

    // 400 of 1000 is 40%, deliberately INSIDE the 24-70 clamp: a bound would also be produced by
    // NaN, so asserting one could not tell a working calculation from a broken one.
    expect(handle).toHaveAttribute("aria-valuenow", "40");
    expect(localStorage.getItem("mrr.drag.split")).toBeNull(); // nothing saved mid-drag

    fireEvent.pointerUp(window);
    expect(localStorage.getItem("mrr.drag.split")).toBe("40");
  });

  it("clamps a drag past the edge to the allowed range", () => {
    const { root, handle } = renderPane("mrr.clamp.split");
    giveRootAWidth(root);

    fireEvent.pointerDown(handle);
    fireEvent.pointerMove(window, { clientX: 950 }); // 95%, past the 70 maximum
    expect(handle).toHaveAttribute("aria-valuenow", "70");

    fireEvent.pointerMove(window, { clientX: 20 }); // 2%, under the 24 minimum
    expect(handle).toHaveAttribute("aria-valuenow", "24");
  });

  it("stops resizing once the handle is released", () => {
    // The drag listeners live on `window`, so a missing cleanup is not visible as a stuck handle -
    // the panes simply keep following the pointer around the page long after the reviewer let go.
    const { root, handle } = renderPane("mrr.release.split");
    giveRootAWidth(root);

    fireEvent.pointerDown(handle);
    fireEvent.pointerMove(window, { clientX: 400 });
    expect(handle).toHaveAttribute("aria-valuenow", "40");

    fireEvent.pointerUp(window);
    fireEvent.pointerMove(window, { clientX: 650 }); // would read 65 if the listener were still on

    expect(handle).toHaveAttribute("aria-valuenow", "40");
  });
});

describe("SplitPane persistence", () => {
  it("restores a width saved from a previous visit", () => {
    localStorage.setItem("mrr.restore.split", "33");
    const { handle } = renderPane("mrr.restore.split");
    expect(handle).toHaveAttribute("aria-valuenow", "33");
  });

  it("ignores a saved width outside the allowed range", () => {
    // Guards against a stored value from an older build, or a hand-edited one, collapsing a pane
    // to nothing with no way back except clearing site data.
    localStorage.setItem("mrr.bad.split", "95");
    const { handle } = renderPane("mrr.bad.split");
    expect(handle).toHaveAttribute("aria-valuenow", "58");
  });
});
