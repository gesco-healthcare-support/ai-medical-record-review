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

/** Three records whose orderings differ on EVERY SORTABLE FIELD ON THE RECORD - not merely on the
 *  three columns under test. That distinction is the whole point of this fixture, and the weaker
 *  version of it is what let a gap survive here: `page_count` and `created_at` are not asserted on,
 *  but both used to sort to the same order as `patient_name`, so an accessor reading either of them
 *  instead would have passed. "Accessor pointed at the neighbouring column" is precisely the
 *  mutation these tests exist to catch.
 *
 *  Three records give six permutations and there are six sortable columns, so there is no slack -
 *  every field below is pinned. Descending, which is how every non-name column opens:
 *
 *    patient_name  Zoe / Adam / Mia          -> A, C, B   asserted
 *    rows_count    1 / 9 / 5                 -> B, C, A   asserted
 *    updated_at    Feb / Mar / Jan           -> B, A, C   asserted
 *    page_count    30 / 20 / 10              -> A, B, C
 *    created_at    Jan-02 / Jan-01 / Jan-03  -> C, A, B
 *    filename      A / B / C                 -> C, B, A
 *
 *  Changing any value here needs that table rechecked, or two fields collide again and the
 *  collision is invisible from the assertions. */
const VARIED: DocumentListItem[] = [
  { ...doc("a", "A.pdf", 30, "2026-01-02T00:00:00Z"), patient_name: "Zoe Last", rows_count: 1, updated_at: "2026-02-01T00:00:00Z" },
  { ...doc("b", "B.pdf", 20, "2026-01-01T00:00:00Z"), patient_name: "Adam First", rows_count: 9, updated_at: "2026-03-01T00:00:00Z" },
  { ...doc("c", "C.pdf", 10, "2026-01-03T00:00:00Z"), patient_name: "Mia Middle", rows_count: 5, updated_at: "2026-01-01T00:00:00Z" },
];

function renderVaried(over: Partial<Parameters<typeof DocumentsTable>[0]> = {}) {
  const props = { docs: VARIED, onOpen: vi.fn(), onIdentify: vi.fn(), onDelete: vi.fn(), ...over };
  return { ...render(<DocumentsTable {...props} />), props };
}

describe("DocumentsTable columns that had no accessor test", () => {
  it("sorts by patient, by documents found, and by last activity", () => {
    // Three of the six accessors were uninvoked. Each reads a DIFFERENT field, and the failure is
    // silent: the rows reorder either way, just by the wrong thing, which nobody notices from a
    // screenshot.
    const { container } = renderVaried();

    // Every column except name opens DESCENDING (`sortDir = key === "name" ? 1 : -1`), which is
    // what a reviewer wants first from "most documents" and "most recent".
    fireEvent.click(header(container, "Patient").querySelector("button")!);
    expect(names(container)).toEqual(["A.pdf", "C.pdf", "B.pdf"]); // Zoe, Mia, Adam

    fireEvent.click(header(container, "Documents found").querySelector("button")!);
    expect(names(container)).toEqual(["B.pdf", "C.pdf", "A.pdf"]); // 9, 5, 1

    fireEvent.click(header(container, "Last activity").querySelector("button")!);
    expect(names(container)).toEqual(["B.pdf", "A.pdf", "C.pdf"]); // Mar, Feb, Jan
  });
});

describe("DocumentsTable search", () => {
  it("returns to the first page, so a search from a later page does not hide its own results", () => {
    // THE FIXTURE IS THE TEST. `curPage = Math.min(page, pageCount - 1)` already rescues a stale
    // page whenever the filtered set collapses to fewer pages - so a search narrowing to ONE page
    // passes with the reset deleted, and proves nothing. Measured: a probe on setPage(0) against
    // that fixture came back NO-OP.
    //
    // This one keeps the filtered set at TWO pages (30 matches, PAGE_SIZE 20), so the clamp cannot
    // help: without the reset the reviewer searches from page 3 and lands on the second page of
    // their own results, with the first twenty matches silently above them.
    const many = [
      ...Array.from({ length: 30 }, (_, i) =>
        doc(`a${i}`, `alpha ${String(i).padStart(2, "0")}.pdf`, 5, "2026-01-01T00:00:00Z"),
      ),
      ...Array.from({ length: 30 }, (_, i) =>
        doc(`b${i}`, `beta ${String(i).padStart(2, "0")}.pdf`, 5, "2026-01-01T00:00:00Z"),
      ),
    ];
    const { container } = render(
      <DocumentsTable docs={many} onOpen={vi.fn()} onIdentify={vi.fn()} onDelete={vi.fn()} />,
    );
    const next = () =>
      [...container.querySelectorAll("button")].find((b) => b.textContent === "Next")!;

    fireEvent.click(next());
    fireEvent.click(next()); // page 3 of 3
    expect(names(container)).not.toContain("alpha 00.pdf");

    fireEvent.change(container.querySelector('input[type="search"]')!, {
      target: { value: "alpha" },
    });

    expect(names(container)).toContain("alpha 00.pdf");
  });
});

describe("DocumentsTable row and menu actions", () => {
  it("opens the record when its row is clicked", () => {
    const { container, props } = renderVaried();
    fireEvent.click(container.querySelector("tbody tr[data-id='a']")!);
    expect(props.onOpen).toHaveBeenCalledWith("a");
  });

  it("does NOT open the record when the actions cell is used", () => {
    // The menu lives inside the clickable row, so without stopPropagation every attempt to reach
    // Delete also navigates into the record - and the reviewer lands somewhere they did not ask for
    // with the menu closed behind them.
    const { container, props } = renderVaried();
    fireEvent.click(container.querySelector("tbody tr[data-id='a'] .hd-menu-cell")!);
    expect(props.onOpen).not.toHaveBeenCalled();
  });
});
