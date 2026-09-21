import { useState, type Ref } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { jumpTo } = vi.hoisted(() => ({ jumpTo: vi.fn() }));

// The PDF pane loads pdf.js in an iframe, so it is stubbed. The stub MUST still forward a ref:
// ReviewEditor navigates the viewer with `pdfRef.current?.jumpTo(...)`, so a stub without one
// leaves `pdfRef.current` null, the optional chain short-circuits, and every assertion about the
// page jump passes having checked nothing - including on a build where the jump was deleted.
vi.mock("@/components/review/pdf-viewer", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    PdfViewer: forwardRef(function PdfViewer(
      _props: unknown,
      ref: Ref<{ jumpTo: (page: number) => void }>,
    ) {
      useImperativeHandle(ref, () => ({ jumpTo }));
      return <div data-testid="pdf" />;
    }),
  };
});

import { ReviewEditor } from "@/components/review/review-editor";
import type { EditorRow } from "@/lib/review-rows";

const row = (over: Partial<EditorRow>): EditorRow => ({
  start: 1,
  end: 2,
  category: "1",
  title: "",
  date: "",
  injury_date: "",
  flag: "-",
  suggest_merge: false,
  include: true,
  _key: "k",
  ...over,
});

const categories = [
  { id: "1", name: "Treating reports" },
  { id: "5", name: "Procedures" },
  { id: "100", name: "General" },
];

/** The editor is controlled, so the filter can only be exercised with a parent that owns the rows -
 *  which is also what proves the count follows an unsaved edit rather than a round trip. */
function Harness({ initial }: { initial: EditorRow[] }) {
  const [rows, setRows] = useState(initial);
  return (
    <ReviewEditor
      documentId="d1"
      filename="f.pdf"
      rows={rows}
      categories={categories}
      totalPages={10}
      onRowsChange={setRows}
    />
  );
}

const titles = () => screen.getAllByLabelText("Document title") as HTMLInputElement[];
const toggle = () => screen.queryByRole("button", { name: /Could not identify/ });

const mixed = [
  row({ _key: "a", start: 1, end: 2, title: "Ruled paperwork", category: "100", ruled_paperwork: true }),
  row({ _key: "b", start: 3, end: 4, title: "Unidentified one", category: "100" }),
  row({ _key: "c", start: 5, end: 6, title: "A treating report", category: "1" }),
];

describe("ReviewEditor could-not-identify filter", () => {
  it("offers no toggle when every document was identified", () => {
    render(<Harness initial={[mixed[0], mixed[2]]} />);
    expect(toggle()).toBeNull();
  });

  it("counts only the documents nothing identified", () => {
    render(<Harness initial={mixed} />);
    expect(toggle()).toHaveTextContent("Could not identify (1)");
  });

  it("shows only those documents once pressed", () => {
    render(<Harness initial={mixed} />);
    expect(titles()).toHaveLength(3);
    fireEvent.click(toggle()!);
    expect(titles().map((i) => i.value)).toEqual(["Unidentified one"]);
    expect(toggle()).toHaveAttribute("aria-pressed", "true");
  });

  it("drops a document from the filter as soon as it is re-categorized, with no save", () => {
    // The live-derivation guard. ruled_paperwork is title-derived and does not change here; the
    // category does, and that alone has to move the row out of the set and drop the count.
    render(<Harness initial={mixed} />);
    fireEvent.click(toggle()!);
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "5" } });
    expect(toggle()).toHaveTextContent("Could not identify (0)");
    expect(screen.getByText("No documents match this filter.")).toBeInTheDocument();
  });

  it("keeps the toggle after the last one is cleared, so the filter can be turned off", () => {
    render(<Harness initial={mixed} />);
    fireEvent.click(toggle()!);
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "5" } });
    fireEvent.click(toggle()!);
    expect(titles()).toHaveLength(3);
    expect(toggle()).toBeNull();
  });
});

const firsts = () => screen.getAllByLabelText("First page") as HTMLInputElement[];
const lasts = () => screen.getAllByLabelText("Last page") as HTMLInputElement[];

beforeEach(() => jumpTo.mockClear());

describe("ReviewEditor shared boundaries", () => {
  // A reviewer reported segmentation landing "off by a page or two", and that fixing it meant
  // "the next several documents are off and I have to manually fix everything". A boundary is one
  // number in the model's answer but two inputs in the table, so every correction cost two edits
  // with the rows overlapping - a blocking error - in between.
  const tiled = () => [
    row({ _key: "a", start: 1, end: 5, title: "First" }),
    row({ _key: "b", start: 6, end: 10, title: "Second" }),
  ];

  it("carries the previous document's last page when a start is corrected", () => {
    render(<Harness initial={tiled()} />);
    fireEvent.change(firsts()[1], { target: { value: "7" } });
    expect(firsts()[1].value).toBe("7");
    expect(lasts()[0].value).toBe("6");
  });

  it("does not close a gap the reviewer left", () => {
    render(
      <Harness
        initial={[
          row({ _key: "a", start: 1, end: 5 }),
          row({ _key: "b", start: 8, end: 10 }),
        ]}
      />,
    );
    fireEvent.change(firsts()[1], { target: { value: "9" } });
    expect(lasts()[0].value).toBe("5");
  });

  it("leaves the next document alone when an end is edited", () => {
    // One-directional on purpose: editing an end is how a reviewer opens a gap deliberately.
    render(<Harness initial={tiled()} />);
    fireEvent.change(lasts()[0], { target: { value: "4" } });
    expect(firsts()[1].value).toBe("6");
  });

  it("leaves every other field of the previous document untouched", () => {
    render(<Harness initial={tiled()} />);
    fireEvent.change(firsts()[1], { target: { value: "7" } });
    expect(titles().map((i) => i.value)).toEqual(["First", "Second"]);
    expect(firsts()[0].value).toBe("1");
  });
});

describe("ReviewEditor row edits", () => {
  it("round-trips every editable field through the parent", () => {
    // The table is presentational and the editor owns the rows, so "the handler fired" is not the
    // guarantee that matters - a handler wired to the wrong field, or dropping the value, fires
    // just as happily. Only the value coming back out proves the edit landed on the right field.
    render(<Harness initial={[row({ _key: "a", start: 1, end: 2 })]} />);

    fireEvent.change(screen.getByLabelText("Document title"), { target: { value: "Knee MRI" } });
    fireEvent.change(screen.getByLabelText("First page"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Last page"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Document date"), { target: { value: "03/04/2026" } });
    fireEvent.change(screen.getByLabelText("Injury date"), { target: { value: "01/02/2025" } });

    expect(screen.getByLabelText("Document title")).toHaveValue("Knee MRI");
    expect(firsts()[0]).toHaveValue(2);
    expect(lasts()[0]).toHaveValue(6);
    expect(screen.getByLabelText("Document date")).toHaveValue("03/04/2026");
    expect(screen.getByLabelText("Injury date")).toHaveValue("01/02/2025");
  });

  it("jumps the viewer to a document's first page when its row is selected", () => {
    const { container } = render(
      <Harness initial={[row({ _key: "a", start: 1, end: 2 }), row({ _key: "b", start: 5, end: 8 })]} />,
    );
    fireEvent.click(container.querySelectorAll("tr.title-row")[1]);
    // The whole point of selecting a row is that the reader lands on that document.
    expect(jumpTo).toHaveBeenCalledWith(5);
  });

  it("merges a document into the one above, keeping the upper document's identity", () => {
    render(
      <Harness
        initial={[
          row({ _key: "a", start: 1, end: 2, title: "First" }),
          row({ _key: "b", start: 3, end: 6, title: "Second" }),
        ]}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: /^Merge up$/ })[0]);

    expect(titles()).toHaveLength(1);
    expect(titles()[0].value).toBe("First"); // merging means "this continues the document above"
    expect(lasts()[0]).toHaveValue(6); // ...but it takes over the lower document's pages
  });

  it("applies every suggested merge at once", () => {
    render(
      <Harness
        initial={[
          row({ _key: "a", start: 1, end: 2 }),
          row({ _key: "b", start: 3, end: 4, suggest_merge: true }),
          row({ _key: "c", start: 5, end: 6, suggest_merge: true }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Apply 2 suggested merges/ }));

    expect(titles()).toHaveLength(1);
    expect(lasts()[0]).toHaveValue(6);
  });

  it("deletes a document", () => {
    render(
      <Harness
        initial={[row({ _key: "a", start: 1, end: 2 }), row({ _key: "b", start: 3, end: 4 })]}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: /^Delete$/ })[0]);
    expect(titles()).toHaveLength(1);
  });

  it("chips a document whose category the cascade guessed", () => {
    // A guessed category ships: the document IS summarized under it, and without the chip nothing
    // on screen says the choice was a coin-flip.
    render(<Harness initial={[row({ _key: "a", category: "5", method: "llm" })]} />);
    expect(screen.getByText("Category guessed")).toBeInTheDocument();
  });
});

describe("ReviewEditor split", () => {
  const long = () => [row({ _key: "a", start: 1, end: 6, title: "Long" })];

  it("splits a document at the chosen page and jumps to the new one", () => {
    render(<Harness initial={long()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Split$/ })); // the row action
    fireEvent.change(screen.getByLabelText("First page of the second document"), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Split$/ })); // the confirm

    expect(titles()).toHaveLength(2);
    expect(lasts()[0]).toHaveValue(3); // the first document now ends the page before the split
    expect(firsts()[1]).toHaveValue(4);
    expect(jumpTo).toHaveBeenCalledWith(4);
  });

  it("refuses a split page outside the document's own span", () => {
    render(<Harness initial={long()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Split$/ }));
    fireEvent.change(screen.getByLabelText("First page of the second document"), {
      target: { value: "9" }, // past this document's last page
    });
    fireEvent.click(screen.getByRole("button", { name: /^Split$/ }));

    // Accepting it would mint a second document over pages the record does not have here.
    expect(titles()).toHaveLength(1);
  });

  it("cancels a split and restores the row's actions", () => {
    render(<Harness initial={long()} />);
    fireEvent.click(screen.getByRole("button", { name: /^Split$/ }));
    expect(screen.getByLabelText("First page of the second document")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));
    expect(screen.queryByLabelText("First page of the second document")).toBeNull();
    expect(titles()).toHaveLength(1);
  });
});

describe("ReviewEditor insert document", () => {
  const openAdd = () => fireEvent.click(screen.getByRole("button", { name: /Insert document/ }));

  it("inserts a document over the pages given and jumps to it", () => {
    render(<Harness initial={[row({ _key: "a", start: 1, end: 2 })]} />);
    openAdd();
    fireEvent.change(screen.getByLabelText("First page of the new document"), {
      target: { value: "5" },
    });
    fireEvent.change(screen.getByLabelText("Last page of the new document"), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Insert$/ }));

    expect(titles()).toHaveLength(2);
    expect(titles()[1].value).toBe("(added manually)");
    expect(firsts()[1]).toHaveValue(5);
    expect(lasts()[1]).toHaveValue(7);
    expect(jumpTo).toHaveBeenCalledWith(5);
  });

  it("refuses a range the record does not have", () => {
    render(<Harness initial={[row({ _key: "a", start: 1, end: 2 })]} />);
    openAdd();
    // Deliberately well-ordered (5 <= 99) so the ONLY thing that can reject this is the page-range
    // check against the record's 10 pages. A start-after-end fixture would still be refused by the
    // ordering test, and the range check could be deleted without any test noticing.
    fireEvent.change(screen.getByLabelText("First page of the new document"), {
      target: { value: "5" },
    });
    fireEvent.change(screen.getByLabelText("Last page of the new document"), {
      target: { value: "99" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Insert$/ }));

    expect(titles()).toHaveLength(1);
  });

  it("cancels without inserting", () => {
    render(<Harness initial={[row({ _key: "a", start: 1, end: 2 })]} />);
    openAdd();
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));

    expect(titles()).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Insert document/ })).toBeInTheDocument();
  });
});
