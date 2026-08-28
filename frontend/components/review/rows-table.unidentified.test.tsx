import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RowsTable } from "@/components/review/rows-table";
import type { EditorRow } from "@/lib/review-rows";

const row = (over: Partial<EditorRow>): EditorRow => ({
  start: 1,
  end: 3,
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

// Three documents with a real page gap between the first and the second, so the gap strip has
// something to render when nothing is filtered.
const three = [
  row({ _key: "a", start: 1, end: 2 }),
  row({ _key: "b", start: 5, end: 6 }),
  row({ _key: "c", start: 7, end: 8 }),
];

function renderTable(over: Record<string, unknown> = {}) {
  return render(
    <RowsTable
      rows={three}
      categories={[{ id: "1", name: "General" }]}
      totalPages={10}
      errors={new Map()}
      selected={-1}
      splitting={-1}
      onSelect={vi.fn()}
      onField={vi.fn()}
      onMergeUp={vi.fn()}
      onSplitStart={vi.fn()}
      onSplitConfirm={vi.fn()}
      onSplitCancel={vi.fn()}
      onDelete={vi.fn()}
      {...over}
    />,
  );
}

describe("RowsTable unidentified rows", () => {
  it("chips a row nothing identified", () => {
    renderTable({ unidentifiedKeys: new Set(["b"]) });
    expect(screen.getAllByText("Could not identify")).toHaveLength(1);
  });

  it("chips nothing when every document was identified", () => {
    renderTable({ unidentifiedKeys: new Set<string>() });
    expect(screen.queryByText("Could not identify")).toBeNull();
  });

  it("does not render a hidden row", () => {
    const { container } = renderTable({ hiddenKeys: new Set(["a", "c"]) });
    expect(container.querySelectorAll("tr.doc-row.title-row")).toHaveLength(1);
  });

  it("keeps a surviving document's number from the FULL set", () => {
    // The regression guard: `#` is `i + 1` over every row, so hiding the first and third must
    // leave the middle one reading 2. Filtering the array before it reaches this table would
    // renumber it to 1 and quietly disagree with the unfiltered view.
    const { container } = renderTable({ hiddenKeys: new Set(["a", "c"]) });
    expect(container.querySelector("td.col-num")?.textContent).toBe("2");
  });

  it("renders the page gap only when nothing is hidden", () => {
    // Pages 3-4 are genuinely skipped between rows a and b, so the strip belongs there...
    const unfiltered = renderTable();
    expect(unfiltered.container.querySelectorAll("tr.gap-row")).toHaveLength(1);
    unfiltered.unmount();
    // ...but once the filter separates the survivors, every remaining pair looks non-contiguous
    // and a strip on each would claim pages were skipped that the reviewer never skipped.
    const filtered = renderTable({ hiddenKeys: new Set(["a"]) });
    expect(filtered.container.querySelector("tr.gap-row")).toBeNull();
  });
});
