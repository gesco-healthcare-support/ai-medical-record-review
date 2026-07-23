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
  _key: `k${Math.random()}`,
  ...over,
});

function renderTable(over: Record<string, unknown> = {}) {
  return render(
    <RowsTable
      rows={[row({ start: 5, end: 5, title: "Laboratory Report" })]}
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

describe("RowsTable attention highlight", () => {
  it("marks the failed row (matched by page range) and shows a 'Could not summarize' chip", () => {
    const { container } = renderTable({ attentionPages: new Set(["5-5"]) });
    expect(screen.getByText("Could not summarize")).toBeInTheDocument();
    // Both the title row and the fields row carry the amber class so the whole record is highlighted.
    expect(container.querySelectorAll("tr.doc-row.attention").length).toBeGreaterThan(0);
  });

  it("marks nothing when there are no failed rows", () => {
    const { container } = renderTable({ attentionPages: new Set<string>() });
    expect(screen.queryByText("Could not summarize")).toBeNull();
    expect(container.querySelector("tr.doc-row.attention")).toBeNull();
  });
});
