import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
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

/** Render RowsTable with stub callbacks; returns every spy plus the container, so a test can assert
 *  on the callback it cares about and reach the row elements that carry no accessible name. */
function renderTable(
  rows: EditorRow[],
  errors = new Map<number, string>(),
  overrides: Partial<ComponentProps<typeof RowsTable>> = {},
) {
  const spies = {
    onSelect: vi.fn(),
    onField: vi.fn(),
    onMergeUp: vi.fn(),
    onSplitStart: vi.fn(),
    onSplitConfirm: vi.fn(),
    onSplitCancel: vi.fn(),
    onDelete: vi.fn(),
  };
  const { container } = render(
    <RowsTable
      rows={rows}
      categories={categories}
      totalPages={10}
      errors={errors}
      selected={-1}
      splitting={-1}
      {...spies}
      {...overrides}
    />,
  );
  return { ...spies, container };
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

  it("wires each row action to its callback with that row's index", async () => {
    // Which button RENDERS and which callback it FIRES are different guarantees, and the tests above
    // only cover the first. A row action that renders correctly and calls the wrong index - or calls
    // nothing - would pass every assertion above.
    const user = userEvent.setup();
    const onMergeUp = vi.fn();
    const onSplitStart = vi.fn();
    const onDelete = vi.fn();
    render(
      <RowsTable
        rows={[
          erow({ start: 1, end: 3 }),
          erow({ start: 4, end: 8, suggest_merge: true }),
        ]}
        categories={categories}
        totalPages={10}
        errors={new Map<number, string>()}
        selected={-1}
        splitting={-1}
        onSelect={vi.fn()}
        onField={vi.fn()}
        onMergeUp={onMergeUp}
        onSplitStart={onSplitStart}
        onSplitConfirm={vi.fn()}
        onSplitCancel={vi.fn()}
        onDelete={onDelete}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Likely same doc/i }));
    expect(onMergeUp).toHaveBeenCalledWith(1);

    onMergeUp.mockClear();
    await user.click(screen.getAllByRole("button", { name: /^Merge up$/ })[0]);
    expect(onMergeUp).toHaveBeenCalledWith(1);

    await user.click(screen.getAllByRole("button", { name: /^Split$/ })[0]);
    expect(onSplitStart).toHaveBeenCalledWith(0);

    await user.click(screen.getAllByRole("button", { name: /^Delete$/ })[0]);
    expect(onDelete).toHaveBeenCalledWith(0);
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
    const { onField } = renderTable([erow({ include: true })]);
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
    const { onField } = renderTable([erow({ flag: "X" })]);
    expect(screen.getByLabelText("Flag for manual review")).toBeChecked(); // 'X' -> checked
    await user.click(screen.getByLabelText("Flag for manual review"));
    expect(onField).toHaveBeenCalledWith(0, { flag: "-" }); // was checked -> unchecks -> '-'
  });

  // A document occupies TWO rows - the title line and the dense fields line - and both must select
  // it. Wiring only one leaves half of every document inert, which reads as an intermittent
  // "clicking does nothing" rather than as a missing handler.
  it("selects the document from either of its two rows", async () => {
    const user = userEvent.setup();
    const { onSelect, container } = renderTable([
      erow({ start: 1, end: 3 }),
      erow({ start: 4, end: 6 }),
    ]);

    const titleRows = container.querySelectorAll("tr.title-row");
    const fieldRows = container.querySelectorAll("tr.doc-row:not(.title-row)");
    expect(titleRows).toHaveLength(2);
    expect(fieldRows).toHaveLength(2);

    await user.click(titleRows[1] as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(1);

    onSelect.mockClear();
    await user.click(fieldRows[0] as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(0);
  });

  it("confirms a split at the page typed into the split box", async () => {
    const user = userEvent.setup();
    const { onSplitConfirm } = renderTable(
      [erow({ start: 1, end: 5 })],
      new Map<number, string>(),
      { splitting: 0 },
    );

    const atPage = screen.getByLabelText("First page of the second document");
    expect(atPage).toHaveValue(2); // defaults to the first page that could start a second document
    await user.clear(atPage);
    await user.type(atPage, "4");
    await user.click(screen.getByRole("button", { name: /^Split$/ }));

    // The typed page, not the default: reading the default would silently split every document
    // after its first page regardless of what the reviewer asked for.
    expect(onSplitConfirm).toHaveBeenCalledWith(0, 4);
  });

  it("cancels a split without confirming one", async () => {
    const user = userEvent.setup();
    const { onSplitCancel, onSplitConfirm } = renderTable(
      [erow({ start: 1, end: 5 })],
      new Map<number, string>(),
      { splitting: 0 },
    );

    await user.click(screen.getByRole("button", { name: /^Cancel$/ }));
    expect(onSplitCancel).toHaveBeenCalled();
    expect(onSplitConfirm).not.toHaveBeenCalled();
  });
});
