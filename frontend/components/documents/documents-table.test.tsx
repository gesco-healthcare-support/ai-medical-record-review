import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/documents-table";
import type { DocumentListItem } from "@/lib/types";

/** Column sorting had no tests, despite being the only way a reviewer finds a record in a long
 *  list. Synthetic records throughout. */
function doc(id: string, filename: string, pages: number, created: string): DocumentListItem {
  return {
    id,
    original_filename: filename,
    page_count: pages,
    status: "uploaded",
    created_at: created,
    updated_at: created,
    active_job: null,
    rows_count: 0,
    patient_first_name: "Synthetic",
    patient_last_name: id.toUpperCase(),
    patient_name: `Synthetic ${id.toUpperCase()}`,
    patient_dob: "01/01/1990",
    law_firm: "Example Firm",
  };
}

const DOCS = [
  doc("a", "Charlie record.pdf", 30, "2026-01-03T00:00:00Z"),
  doc("b", "alpha record.pdf", 10, "2026-01-01T00:00:00Z"),
  doc("c", "Bravo record.pdf", 20, "2026-01-02T00:00:00Z"),
];

function renderTable() {
  return render(
    <DocumentsTable docs={DOCS} onOpen={vi.fn()} onIdentify={vi.fn()} onDelete={vi.fn()} />,
  );
}

function names(container: HTMLElement) {
  return [...container.querySelectorAll("tbody .hd-name")].map((n) => n.textContent);
}

function header(container: HTMLElement, label: string) {
  return [...container.querySelectorAll("thead th")].find((th) =>
    th.textContent?.includes(label),
  ) as HTMLTableCellElement;
}

describe("DocumentsTable sorting", () => {
  it("defaults to newest upload first", () => {
    const { container } = renderTable();
    expect(names(container)).toEqual([
      "Charlie record.pdf",
      "Bravo record.pdf",
      "alpha record.pdf",
    ]);
  });

  it("sorts by name, case-insensitively, and reverses on a second click", () => {
    const { container } = renderTable();
    const nameHeader = header(container, "Document");

    fireEvent.click(nameHeader.querySelector("button")!);
    // Case-insensitive: "alpha" sorts first despite the capitals on the other two.
    expect(names(container)).toEqual([
      "alpha record.pdf",
      "Bravo record.pdf",
      "Charlie record.pdf",
    ]);
    expect(nameHeader.getAttribute("aria-sort")).toBe("ascending");

    fireEvent.click(nameHeader.querySelector("button")!);
    expect(names(container)).toEqual([
      "Charlie record.pdf",
      "Bravo record.pdf",
      "alpha record.pdf",
    ]);
    expect(nameHeader.getAttribute("aria-sort")).toBe("descending");
  });

  it("sorts page count numerically, largest first", () => {
    // Only the name column opens ascending; every other column opens on its largest or newest
    // value, which is what a reviewer scanning for the big or recent record wants first.
    const { container } = renderTable();
    fireEvent.click(header(container, "Pages").querySelector("button")!);
    expect(names(container)).toEqual([
      "Charlie record.pdf",
      "Bravo record.pdf",
      "alpha record.pdf",
    ]);

    fireEvent.click(header(container, "Pages").querySelector("button")!);
    expect(names(container)).toEqual([
      "alpha record.pdf",
      "Bravo record.pdf",
      "Charlie record.pdf",
    ]);
  });

  it("marks only the sorted column for assistive technology", () => {
    const { container } = renderTable();
    fireEvent.click(header(container, "Document").querySelector("button")!);
    const sorted = [...container.querySelectorAll("thead th")].filter(
      (th) => th.getAttribute("aria-sort") && th.getAttribute("aria-sort") !== "none",
    );
    expect(sorted).toHaveLength(1);
    expect(sorted[0].textContent).toContain("Document");
  });
});
