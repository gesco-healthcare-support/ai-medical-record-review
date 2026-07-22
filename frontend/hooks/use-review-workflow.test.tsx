import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The hook calls the review-api module directly (no react-query), so mock that module. The stub
// factory references nothing external, so vitest's hoisting of vi.mock above the imports is safe.
vi.mock("@/lib/review-api", () => ({
  getDocument: vi.fn(),
  getStatus: vi.fn(),
  saveRows: vi.fn(),
  startSegment: vi.fn(),
  startSummarize: vi.fn(),
}));

import { useReviewWorkflow } from "@/hooks/use-review-workflow";
import { getDocument, getStatus, saveRows } from "@/lib/review-api";
import type { DocumentDetail } from "@/lib/types";

const mockDoc = vi.mocked(getDocument);
const mockStatus = vi.mocked(getStatus);
const mockSave = vi.mocked(saveRows);

const detail = (over: Partial<DocumentDetail> = {}): DocumentDetail => ({
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
  rows: [
    {
      start: 1,
      end: 3,
      category: "1",
      title: "",
      date: "",
      injury_date: "",
      flag: "-",
      suggest_merge: false,
      include: true,
    },
  ],
  categories: [],
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useReviewWorkflow boot routing", () => {
  it("stays idle (loading) when the documentId is null", () => {
    const { result } = renderHook(() => useReviewWorkflow(null));
    expect(result.current.section).toBe("loading");
    expect(mockDoc).not.toHaveBeenCalled();
  });

  it("routes a finished document to the summaries step", async () => {
    mockDoc.mockResolvedValue(detail({ status: "done" }));
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("summaries"));
  });

  it("routes a finished document to the editor when summaries are disabled (bundle mode)", async () => {
    mockDoc.mockResolvedValue(detail({ status: "done" }));
    const { result } = renderHook(() => useReviewWorkflow("d1", { enableSummaries: false }));
    await waitFor(() => expect(result.current.section).toBe("editor"));
  });

  it("opens the editor for a reviewing document that has rows", async () => {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));
  });

  it("shows the start panel for an uploaded document with no rows", async () => {
    mockDoc.mockResolvedValue(detail({ status: "uploaded", rows: [] }));
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("start"));
  });

  it("watches a running segment job and lands in the editor when it finishes", async () => {
    mockDoc
      .mockResolvedValueOnce(
        detail({
          status: "segmenting",
          active_job: {
            kind: "segment",
            state: "running",
            stage: "segmenting",
            current: 1,
            total: 5,
            error: null,
          },
        }),
      )
      .mockResolvedValueOnce(detail({ status: "reviewing" }));
    mockStatus.mockResolvedValue({ status: "reviewing", job: null });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));
  });
});

// Regression guards for the resumable-summarize states (PR #28) + the error branch. Expected
// section/attention/banner are derived from the documented state machine, not the hook output.
describe("useReviewWorkflow resumable-summarize + error states", () => {
  it("routes a needs_attention document to the editor with the attention notice", async () => {
    mockDoc.mockResolvedValue(detail({ status: "needs_attention" }));
    mockStatus.mockResolvedValue({
      status: "needs_attention",
      job: {
        kind: "summarize",
        state: "needs_attention",
        stage: "summarizing",
        current: 0,
        total: 0,
        error: "Two documents need attention.",
      },
    });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));
    expect(result.current.attention?.message).toBe("Two documents need attention.");
  });

  it("surfaces needs_attention from a summarize run as a calm notice, not an error", async () => {
    // Realistic path: the reviewer is in the editor and clicks Summarize; the run ends
    // needs_attention. The notice is set, no error banner, and they stay in the editor to fix it.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" })); // rows present, no active job
    mockStatus.mockResolvedValue({
      status: "needs_attention",
      job: {
        kind: "summarize",
        state: "needs_attention",
        stage: "summarizing",
        current: 1,
        total: 1,
        error: "One document could not be summarized.",
      },
    });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));

    await act(async () => {
      await result.current.onSummarize();
    });
    expect(result.current.attention?.message).toBe("One document could not be summarized.");
    expect(result.current.section).toBe("editor");
    expect(result.current.banner).toBe(""); // calm terminal state, not the scary error path
  });

  it("keeps showing progress while a job is paused (auto-resuming, not terminal)", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "summarizing",
        active_job: {
          kind: "summarize",
          state: "running",
          stage: "summarizing",
          current: 0,
          total: 5,
          error: null,
        },
      }),
    );
    mockStatus.mockResolvedValue({
      status: "summarizing",
      job: { kind: "summarize", state: "paused", stage: "paused", current: 0, total: 5, error: null },
    });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("progress"));
    expect(result.current.banner).toBe(""); // paused must not resolve to an error
  });

  it("shows a failure banner for an errored document", async () => {
    mockDoc.mockResolvedValue(detail({ status: "error", rows: [] }));
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("start"));
    expect(result.current.banner).toBe("The last run failed - you can start again.");
  });
});

// The autosave data-integrity guard: valid rows are persisted (stripped of the client _key);
// invalid/overlapping rows must NEVER reach the server (they would drive bad PDF slicing).
describe("useReviewWorkflow autosave gating", () => {
  const editorRow = (over: Record<string, unknown> = {}) => ({
    start: 1,
    end: 3,
    category: "1",
    title: "",
    date: "",
    injury_date: "",
    flag: "-",
    suggest_merge: false,
    include: true,
    _key: "k1",
    ...over,
  });

  it("autosaves valid rows without the client _key after the debounce", async () => {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" })); // page_count 10
    mockSave.mockResolvedValue({ ok: true, count: 1 });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() => result.current.onRowsChange([editorRow({ start: 2, end: 5 })]));
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledWith("d1", [expect.objectContaining({ start: 2, end: 5 })]);
    expect(mockSave.mock.calls[0][1][0]).not.toHaveProperty("_key");
  });

  it("does NOT autosave an invalid (overlapping) row set", async () => {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockResolvedValue({ ok: true, count: 0 });
    const { result } = renderHook(() => useReviewWorkflow("d1"));
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() =>
      result.current.onRowsChange([
        editorRow({ _key: "a", start: 1, end: 5 }),
        editorRow({ _key: "b", start: 3, end: 7 }), // overlaps the previous row
      ]),
    );
    await new Promise((resolve) => setTimeout(resolve, 900)); // past the 800ms debounce
    expect(mockSave).not.toHaveBeenCalled();
    expect(result.current.saveState.kind).toBe("dirty");
  });
});
