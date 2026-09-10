import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RowsTable } from "@/components/review/rows-table";
import type { EditorRow } from "@/lib/review-rows";
import type { CategoryOption } from "@/lib/types";

let seq = 0;
const erow = (over: Partial<EditorRow> = {}): EditorRow => ({
  _key: `k${seq++}`,
  start: 1,
  end: 3,
  category: "1",
  title: "",
  date: "",
  injury_date: "",
  flag: "-",
  suggest_merge: false,
  include: true,
  ...over,
});

const categories: CategoryOption[] = [{ id: "1", name: "Progress report" }];

/** Render RowsTable with stub callbacks; returns the onField spy for interaction assertions. */
function renderTable(rows: EditorRow[], errors = new Map<number, string>()) {
  const onField = vi.fn();
  render(
    <RowsTable
      rows={rows}
      categories={categories}
      totalPages={10}
      errors={errors}
      selected={-1}
      splitting={-1}
      onSelect={vi.fn()}
      onField={onField}
      onMergeUp={vi.fn()}
      onSplitStart={vi.fn()}
      onSplitConfirm={vi.fn()}
      onSplitCancel={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
  return onField;
}

describe("RowsTable", () => {
  it("renders the page-range inputs from the row values", () => {
    renderTable([erow({ start: 2, end: 5 })]);
    expect(screen.getByLabelText("First page")).toHaveValue(2);
    expect(screen.getByLabelText("Last page")).toHaveValue(5);
  });

  // The three row-action buttons below are each conditional, and NOTHING covered those conditions
  // before these tests: breaking all three - rendering each button unconditionally - left every
  // existing test in this file green. They are pinned here because the row-actions block is about to
  // move into its own component, and a green suite that cannot see the move is not a safety net.

  it("offers the merge suggestion only on a suggested row that has a row above it", () => {
    renderTable([
      erow({ suggest_merge: true }), // suggested, but FIRST - nothing to merge into
      erow({ start: 4, end: 6, suggest_merge: true }), // suggested and has a predecessor
      erow({ start: 7, end: 9, suggest_merge: false }), // not suggested
    ]);
    expect(
      screen.getAllByRole("button", { name: /Likely same doc/i }),
    ).toHaveLength(1);
  });

  it("offers Merge up on every row except the first", () => {
    renderTable([
      erow(),
      erow({ start: 4, end: 6 }),
      erow({ start: 7, end: 9 }),
    ]);
    expect(screen.getAllByRole("button", { name: /^Merge up$/ })).toHaveLength(
      2,
    );
  });

  it("offers Split only on a row spanning more than one page", () => {
    renderTable([erow({ start: 1, end: 3 }), erow({ start: 4, end: 4 })]);
    expect(screen.getAllByRole("button", { name: /^Split$/ })).toHaveLength(1);
  });

  it("marks only the invalid row's fields row with the invalid class", () => {
    const { container } = render(
      <RowsTable
        rows={[erow(), erow({ start: 3, end: 2 })]}
        categories={categories}
        totalPages={10}
        errors={new Map([[1, "bad"]])}
        selected={-1}
        splitting={-1}
        onSelect={vi.fn()}
        onField={vi.fn()}
        onMergeUp={vi.fn()}
        onSplitStart={vi.fn()}
        onSplitConfirm={vi.fn()}
        onSplitCancel={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const invalidRows = container.querySelectorAll("tr.invalid");
    expect(invalidRows).toHaveLength(1);
    // ...and it is the SECOND document's fields row (start=3), not the first row or a title row.
    expect(
      within(invalidRows[0] as HTMLElement).getByLabelText("First page"),
    ).toHaveValue(3);
  });

  it("reflects the include-in-summarization checkbox state", () => {
    renderTable([erow({ include: false })]);
    expect(screen.getByLabelText("Include in summarization")).not.toBeChecked();
  });

  it("emits an include toggle via onField", async () => {
    const user = userEvent.setup();
    const onField = renderTable([erow({ include: true })]);
    await user.click(screen.getByLabelText("Include in summarization"));
    expect(onField).toHaveBeenCalledWith(0, { include: false });
  });

  it("shows a gap strip between non-contiguous documents", () => {
    renderTable([erow({ start: 1, end: 3 }), erow({ start: 5, end: 6 })]);
    expect(screen.getByText(/pages 4-4 not included/)).toBeInTheDocument();
  });

  it("shows NO gap strip for contiguous documents (boundary: start == prevEnd + 1)", () => {
    renderTable([erow({ start: 1, end: 3 }), erow({ start: 4, end: 6 })]);
    expect(screen.queryByText(/not included/)).toBeNull();
  });

  it("reflects and toggles the review flag (case-insensitive 'x')", async () => {
    const user = userEvent.setup();
    const onField = renderTable([erow({ flag: "X" })]);
    expect(screen.getByLabelText("Flag for manual review")).toBeChecked(); // 'X' -> checked
    await user.click(screen.getByLabelText("Flag for manual review"));
    expect(onField).toHaveBeenCalledWith(0, { flag: "-" }); // was checked -> unchecks -> '-'
  });
});
