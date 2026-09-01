import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// #204: `touchedRef` used to survive a document switch, and the only thing stopping a stale
// `_key:field` entry from pinning a field on the NEXT document's rows was that `keySeq` is a
// module-global that is never reset - so a key minted for document A could never be minted again
// for document B. Safe by accident: nothing said so, and "reset keySeq per document" is a
// reasonable-looking cleanup.
//
// That is also why this file exists rather than another block in use-review-workflow.test.tsx.
// With globally unique keys the leak is UNOBSERVABLE - which is the whole point of the issue - so
// demonstrating it needs `withKeys` to restart its numbering on every call, exactly as the tidy-up
// would make it. The mock is hoisted module-wide by vitest, so it lives in its own file where it
// cannot change key identity for the tests that pass their own `_key` values.
vi.mock("@/lib/review-rows", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/review-rows")>();
  return {
    ...actual,
    withKeys: (rows: import("@/lib/types").Row[]) =>
      rows.map((row, i) => ({ ...row, _key: `k${i + 1}` })),
  };
});

vi.mock("@/lib/review-api", () => ({
  cancelJob: vi.fn(),
  getDocument: vi.fn(),
  getStatus: vi.fn(),
  saveRows: vi.fn(),
  startDedup: vi.fn(),
  startSegment: vi.fn(),
  startSummarize: vi.fn(),
}));

import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { getDocument, saveRows } from "@/lib/review-api";
import type { DocumentDetail } from "@/lib/types";

const mockDoc = vi.mocked(getDocument);
const mockSave = vi.mocked(saveRows);

function renderWorkflow(documentId: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(({ id }: { id: string | null }) => useReviewWorkflow(id), {
    wrapper,
    initialProps: { id: documentId },
  });
}

const row = (over: Record<string, unknown> = {}) => ({
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

const detail = (over: Partial<DocumentDetail> = {}): DocumentDetail =>
  ({
    id: "d1",
    original_filename: "f.pdf",
    page_count: 10,
    status: "reviewing",
    created_at: "",
    updated_at: "",
    active_job: null,
    patient_first_name: "",
    patient_last_name: "",
    patient_name: "",
    patient_dob: "",
    law_firm: "",
    rows: [row()],
    categories: [],
    ...over,
  }) as DocumentDetail;

describe("the touched set does not cross a document boundary", () => {
  beforeEach(() => vi.clearAllMocks());

  it("drops document A's touched fields when document B loads", async () => {
    // A: the reviewer re-classifies, and the save fails - so the buffer holds the only copy and
    // "k1:category" is in the touched set.
    mockDoc.mockResolvedValue(detail());
    mockSave.mockRejectedValue(new Error("boom"));
    const { result, rerender } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));
    act(() => result.current.onRowsChange(result.current.rows.map((r) => ({ ...r, category: "13" }))));
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));

    // B: a different document, whose row takes the same key under this mock.
    mockDoc.mockResolvedValue(detail({ id: "d2", rows: [row({ category: "1" })] }));
    rerender({ id: "d2" });
    await waitFor(() => expect(result.current.rows[0]?.category).toBe("1"));

    // Put B into an unsaved state via a field the touched set does not track, so the only thing
    // that could withhold the server's category is a leftover entry from A.
    act(() => result.current.onRowsChange(result.current.rows.map((r) => ({ ...r, title: "t" }))));
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));

    // Another tab re-classifies B's row. It must land: nothing on B has been touched.
    mockDoc.mockResolvedValue(detail({ id: "d2", rows: [row({ category: "5" })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].category).toBe("5");
    expect(result.current.rows[0].title).toBe("t"); // B's own unsaved edit still survives
  });

  it("still protects an unsaved edit made on the document now on screen", async () => {
    // The complement, so the clear cannot be "fixed" by never protecting anything.
    mockDoc.mockResolvedValue(detail({ id: "d2" }));
    mockSave.mockRejectedValue(new Error("boom"));
    const { result } = renderWorkflow("d2");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() => result.current.onRowsChange(result.current.rows.map((r) => ({ ...r, category: "13" }))));
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));

    mockDoc.mockResolvedValue(detail({ id: "d2", rows: [row({ category: "5" })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].category).toBe("13");
  });
});
