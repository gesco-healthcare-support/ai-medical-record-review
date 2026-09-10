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

describe("DocumentsTable status filters", () => {
  function withStatus(id: string, status: DocumentListItem["status"]): DocumentListItem {
    return { ...doc(id, `${id}.pdf`, 1, "2026-01-01T00:00:00Z"), status };
  }

  function chip(container: HTMLElement, label: string) {
    return [...container.querySelectorAll(".hd-chip")].find((c) =>
      c.textContent?.startsWith(label),
    );
  }

  it("does not count an interrupted record as Failed", () => {
    // `interrupted` is a restart orphan, and the backend says so in one place on purpose:
    // `worker/failures._STATE_OUTCOMES` maps it to `orphaned`, "NOT a failure", and `is_failure`
    // excludes it. #218 measured what conflating them costs. This chip was the last surface still
    // giving the naive reading, under a label that tells the reviewer their record broke.
    const { container } = render(
      <DocumentsTable
        docs={[withStatus("a", "error"), withStatus("b", "interrupted")]}
        onOpen={vi.fn()}
      />,
    );

    expect(chip(container, "Failed")?.textContent).toContain("1");
    expect(chip(container, "Interrupted")?.textContent).toContain("1");
  });

  it("filters to the interrupted record on its own chip", () => {
    const { container } = render(
      <DocumentsTable
        docs={[withStatus("a", "error"), withStatus("b", "interrupted")]}
        onOpen={vi.fn()}
      />,
    );
    fireEvent.click(chip(container, "Interrupted")!);
    expect(names(container)).toEqual(["b.pdf"]);
  });
});

describe("DocumentsTable paging when the list shrinks underneath it", () => {
  function many(n: number): DocumentListItem[] {
    return Array.from({ length: n }, (_, i) =>
      doc(`d${i}`, `rec-${String(i).padStart(3, "0")}.pdf`, 1, "2026-01-01T00:00:00Z"),
    );
  }

  function footer(container: HTMLElement) {
    return container.querySelector(".hd-foot span")?.textContent;
  }

  it("steps back from the page on screen, not from a stale page number", () => {
    // `page` is what the buttons set and `curPage` is what is SHOWN, clamped when the list
    // shrinks - deleting the last row of the last page, or a poll returning fewer records. Prev
    // stepped back from the STALE `page`, landing on the page already shown, so the click did
    // nothing.
    const { container, rerender } = render(
      <DocumentsTable docs={many(41)} onOpen={vi.fn()} />,
    );
    const nav = (name: string) =>
      [...container.querySelectorAll("button")].find((b) => b.textContent === name)!;
    const next = () => fireEvent.click(nav("Next"));
    const prev = () => fireEvent.click(nav("Prev"));

    next();
    next();
    expect(footer(container)).toBe("41-41 of 41"); // page index 2 of 0-2

    rerender(<DocumentsTable docs={many(21)} onOpen={vi.fn()} />);
    expect(footer(container)).toBe("21-21 of 21"); // clamped to page index 1 of 0-1

    prev();
    expect(footer(container)).toBe("1-20 of 21");
  });
});
