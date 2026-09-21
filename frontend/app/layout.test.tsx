/**
 * What the root layout guarantees for every route at once.
 *
 * Four other files already lean on the first of these in their comments and nothing anywhere
 * pinned it (`git grep -l "lives in the root" -- frontend` finds 4: category-dialog.tsx:75 and
 * its test, prompt-dialog.tsx:100 and its test). Note that the obvious pattern, "root layout",
 * finds only 3 - category-dialog.tsx:75 wraps between "root" and "layout", so a line-oriented
 * grep cannot see it.
 *
 * Two measured facts shape every test here:
 *
 * 1. React 19 HOISTS <html> and <body> into the real document rather than nesting them in Testing
 *    Library's container, so `children` and the toast host end up as SIBLINGS in the container and
 *    the layout's className lands on `document.documentElement`.
 * 2. A Toaster with no toasts renders an EMPTY <section> - no <ol>, no position attributes, no
 *    styling. Every guarantee about how a toast looks therefore has to raise a real one first.
 */
import { render, screen } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * `next/font/google` resolves to a ZERO-BYTE file at runtime - `wc -c
 * node_modules/next/font/google/index.js` is 0, because Next's SWC plugin rewrites `Inter({...})`
 * at build time and leaves nothing for a test runner to import. Unmocked, importing the layout
 * throws "(0 , __vite_ssr_import_0__.Inter) is not a function". layout.tsx is the only importer in
 * the repo, which is why no mock existed here before.
 *
 * This mock also BOUNDS what the font test below can mean: the variable strings are this mock's,
 * not the app's. That test therefore pins COMPOSITION - both font objects reach <html> - and says
 * nothing whatever about either variable being named --font-inter or --font-poppins.
 */
vi.mock("next/font/google", () => ({
  Inter: () => ({ variable: "mock-inter-var", className: "mock-inter", style: {} }),
  Poppins: () => ({ variable: "mock-poppins-var", className: "mock-poppins", style: {} }),
}));

import RootLayout from "@/app/layout";

/** sonner's toast store is module-global and outlives an unmount, so a toast raised by one test
 *  reappears the moment the next test mounts a fresh Toaster. Every test below uses its own
 *  message and scopes off that message's own element rather than querying the first toast in the
 *  document, and this clears the store between them as well. */
afterEach(() => {
  toast.dismiss();
});

function renderLayout() {
  return render(
    <RootLayout>
      <p data-testid="page-content">the record the reviewer had open</p>
    </RootLayout>,
  );
}

/** Raises one toast and returns ITS list and item, found by walking up from its own text. */
async function raiseToast(raise: () => void, message: string) {
  raise();
  const title = await screen.findByText(message, {}, { timeout: 4000 });
  const item = title.closest("[data-sonner-toast]");
  const list = title.closest("[data-sonner-toaster]");
  expect(item).not.toBeNull();
  expect(list).not.toBeNull();
  return { item: item as HTMLElement, list: list as HTMLElement };
}

describe("the root layout", () => {
  it("mounts the toast host beside the page content, not inside it", () => {
    const { container } = renderLayout();

    const content = screen.getByTestId("page-content");
    const host = screen.getByRole("region", { name: /notifications/i });

    // Siblings, not ancestor and descendant. This is the property the four files above rely on:
    // a toast raised by a dialog survives the dialog closing, because unmounting the page content
    // cannot take the host down with it. Nesting the host inside `children` would satisfy "both
    // are on screen" and break exactly that.
    expect(container).toContainElement(content);
    expect(container).toContainElement(host);
    expect(content).not.toContainElement(host);
    expect(host).not.toContainElement(content);
  });

  it("puts toasts at the bottom centre rather than sonner's default corner", async () => {
    renderLayout();

    const { list } = await raiseToast(
      () => toast("the export is ready to download"),
      "the export is ready to download",
    );

    // MEASURED: a bare <Sonner /> with NO props renders data-y-position="bottom" alongside
    // data-x-position="right". So `x` is the only half of this that discriminates - `y` matches
    // sonner's own default and passes with position="bottom-center" deleted. Both are asserted
    // because the contract is "bottom centre", but do not read `y` as evidence the prop is wired.
    expect(list.getAttribute("data-x-position")).toBe("center");
    expect(list.getAttribute("data-y-position")).toBe("bottom");
  });

  it("dresses toasts in the app's navy surface rather than sonner's own", async () => {
    renderLayout();

    const { list } = await raiseToast(
      () => toast("the summary could not be saved"),
      "the summary could not be saved",
    );

    // MEASURED: bare sonner renders class="" and sets no --normal-bg at all, so both of these are
    // ABSENT without the wrapper's props rather than merely different - there is no default here
    // that could satisfy the assertion on the wrapper's behalf.
    expect(list.className).toContain("toaster group");
    expect(list.style.getPropertyValue("--normal-bg").trim()).toBe("var(--color-navy-900)");
  });

  it("uses the app's own icon set for a success toast", async () => {
    renderLayout();

    const { item } = await raiseToast(
      () => toast.success("the record was sent for review"),
      "the record was sent for review",
    );

    // MEASURED: sonner's built-in success icon is its own viewBox="0 0 20 20" fill="currentColor"
    // path, carrying no class. The lucide class is what proves the wrapper's `icons` prop reached
    // sonner rather than being silently ignored.
    const icon = item.querySelector("[data-icon] svg");
    expect(icon?.getAttribute("class")).toContain("lucide-circle-check");
  });

  it("exposes both font families to CSS on the document element", () => {
    // The className lands on the REAL document element (hoisting, above), which outlives a single
    // test's container - so prove the channel is empty first, or a leftover from an earlier render
    // would satisfy this test without the layout contributing anything.
    expect(document.documentElement.className).toBe("");

    renderLayout();

    // COMPOSITION, not naming: both strings come from the next/font mock at the top of this file,
    // so this cannot tell you the variable is called --font-poppins. What it catches is a font
    // dropping out of the className - the silent failure, because losing Poppins leaves every
    // heading falling back to the body face with nothing anywhere complaining.
    const classes = document.documentElement.className;
    expect(classes).toContain("mock-inter-var");
    expect(classes).toContain("mock-poppins-var");
  });
});
