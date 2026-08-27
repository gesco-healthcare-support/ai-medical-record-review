import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The PDF pane loads pdf.js in an iframe and has nothing to do with the filter under test.
vi.mock("@/components/review/pdf-viewer", () => ({
  PdfViewer: () => <div data-testid="pdf" />,
}));

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
