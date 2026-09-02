import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The hook calls the review-api module directly, so mock that module. The stub factory references
// nothing external, so vitest's hoisting of vi.mock above the imports is safe.
//
// It ALSO holds a react-query client now, to drop the cached summaries when a summarize run settles
// (nothing else refreshed that query, so "Re-summarize all" left the old text on screen). So every
// render goes through `renderWorkflow`, which supplies a provider and hands the client back.
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
import {
  cancelJob,
  getDocument,
  getStatus,
  saveRows,
  startSegment,
  startSummarize,
} from "@/lib/review-api";
import type { DocumentDetail, DocumentStatus, JobState } from "@/lib/types";

const mockDoc = vi.mocked(getDocument);
const mockStatus = vi.mocked(getStatus);
const mockSave = vi.mocked(saveRows);
const mockCancel = vi.mocked(cancelJob);
const mockStartSegment = vi.mocked(startSegment);
const mockStartSummarize = vi.mocked(startSummarize);

function renderWorkflow(...args: Parameters<typeof useReviewWorkflow>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => useReviewWorkflow(...args), { wrapper }), client };
}

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
    const { result } = renderWorkflow(null);
    expect(result.current.section).toBe("loading");
    expect(mockDoc).not.toHaveBeenCalled();
  });

  it("routes a finished document to the summaries step", async () => {
    mockDoc.mockResolvedValue(detail({ status: "done" }));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("summaries"));
  });

  it("routes a finished document to the editor when summaries are disabled (bundle mode)", async () => {
    mockDoc.mockResolvedValue(detail({ status: "done" }));
    const { result } = renderWorkflow("d1", { enableSummaries: false });
    await waitFor(() => expect(result.current.section).toBe("editor"));
  });

  it("opens the editor for a reviewing document that has rows", async () => {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));
  });

  it("shows the start panel for an uploaded document with no rows", async () => {
    mockDoc.mockResolvedValue(detail({ status: "uploaded", rows: [] }));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("start"));
  });

  it("watches a running segment job and lands in the editor when it finishes", async () => {
    mockDoc
      .mockResolvedValueOnce(
        detail({
          status: "segmenting",
          active_job: {
            id: 1, kind: "segment",
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
    const { result } = renderWorkflow("d1");
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
        id: 1, kind: "summarize",
        state: "needs_attention",
        stage: "summarizing",
        current: 0,
        total: 0,
        error: "Two documents need attention.",
      },
    });
    const { result } = renderWorkflow("d1");
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
        id: 1, kind: "summarize",
        state: "needs_attention",
        stage: "summarizing",
        current: 1,
        total: 1,
        error: "One document could not be summarized.",
      },
    });
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    await act(async () => {
      await result.current.onSummarize();
    });
    expect(result.current.attention?.message).toBe("One document could not be summarized.");
    expect(result.current.section).toBe("editor");
    expect(result.current.banner).toBe(""); // calm terminal state, not the scary error path
  });

  // (job state, resulting document status). A cancelled job leaves the document "reviewing" - there
  // is no "cancelled" document status, which is itself the point: the run still committed rows.
  it.each<[JobState, DocumentStatus]>([
    ["done", "done"],
    ["needs_attention", "needs_attention"],
    ["cancelled", "reviewing"],
  ])("drops the cached summaries when a summarize run settles as %s", async (state, status) => {
    // The run REPLACED the stored summaries, so the cached copy has to go. All three outcomes leave
    // new rows behind: "done" rewrote every included row, "needs_attention" keeps the partial
    // results, and "cancelled" commits whatever finished before the stop.
    //
    // Nothing else refreshed this query - a bare useQuery with staleTime 30s, no refetchInterval, and
    // a per-edit setQueryData as its only other writer - so its sole refresh trigger was a NEW
    // observer mounting. "Re-summarize all from scratch" is the one path that cannot get that: the
    // button only renders on the Summaries tab, the view stays mounted for the whole run, and the
    // tab is then set to the value it already holds. So the tab kept rendering the pre-run text with
    // nothing to say it was stale, and editing a card from that view wrote the OLD body over the
    // freshly generated one, because Summary.idx is positional over included rows.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockStatus.mockResolvedValue({
      status,
      job: {
        id: 1,
        kind: "summarize",
        state,
        stage: "summarizing",
        current: 1,
        total: 1,
        error: state === "needs_attention" ? "One document could not be summarized." : "",
      },
    });
    const { result, client } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    const invalidate = vi.spyOn(client, "invalidateQueries");
    await act(async () => {
      await result.current.onSummarize();
    });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["summaries", "d1"] });
  });

  it("keeps showing progress while a job is paused (auto-resuming, not terminal)", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "summarizing",
        active_job: {
          id: 1, kind: "summarize",
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
      job: { id: 1, kind: "summarize", state: "paused", stage: "paused", current: 0, total: 5, error: null },
    });
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("progress"));
    expect(result.current.banner).toBe(""); // paused must not resolve to an error
  });

  it("routes a boot-active summarize job ending needs_attention to the editor, not the start panel", async () => {
    // Regression guard for the stale-rows-closure bug: a summarize job discovered at boot that ends
    // needs_attention must land in the editor (rows present) with the notice, via rowsRef.
    mockDoc.mockResolvedValue(
      detail({
        status: "summarizing",
        active_job: {
          id: 1, kind: "summarize",
          state: "running",
          stage: "summarizing",
          current: 1,
          total: 2,
          error: null,
        },
      }),
    );
    mockStatus.mockResolvedValue({
      status: "needs_attention",
      job: {
        id: 1, kind: "summarize",
        state: "needs_attention",
        stage: "summarizing",
        current: 2,
        total: 2,
        error: "One document needs attention.",
      },
    });
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));
    expect(result.current.attention?.message).toBe("One document needs attention.");
  });

  it("shows a failure banner for an errored document", async () => {
    mockDoc.mockResolvedValue(detail({ status: "error", rows: [] }));
    const { result } = renderWorkflow("d1");
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
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() => result.current.onRowsChange([editorRow({ start: 2, end: 5 })]));
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledWith("d1", [expect.objectContaining({ start: 2, end: 5 })]);
    expect(mockSave.mock.calls[0][1][0]).not.toHaveProperty("_key");
  });

  it("does NOT autosave an invalid (overlapping) row set", async () => {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockResolvedValue({ ok: true, count: 0 });
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() =>
      result.current.onRowsChange([
        editorRow({ _key: "a", start: 1, end: 5 }),
        editorRow({ _key: "b", start: 3, end: 7 }), // overlaps the previous row
      ]),
    );
    await new Promise((resolve) => setTimeout(resolve, 900)); // past the 800ms debounce
    expect(mockSave).not.toHaveBeenCalled();
    // Loud, not silent: the state flips to error with a fix-the-rows message (Summarize stays blocked).
    expect(result.current.saveState.kind).toBe("error");
    expect(result.current.saveState.message).toMatch(/fix the highlighted page ranges/i);
  });
});

// The stop/restart cycle. The critical one is the FIRST test: `cancelled` is not in the poller's
// keep-polling exclusion list by accident, and if it falls through there the progress bar spins
// forever - a working stop button that looks broken.
describe("useReviewWorkflow stop and restart", () => {
  it("settles a cancelled run instead of polling forever", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "segmenting",
        active_job: {
          id: 7,
          kind: "segment",
          state: "running",
          stage: "segmenting",
          current: 1,
          total: 5,
          error: null,
        },
      }),
    );
    mockStatus.mockResolvedValue({
      status: "uploaded",
      job: {
        id: 7,
        kind: "segment",
        state: "cancelled",
        stage: "cancelled",
        current: 1,
        total: 5,
        error: null,
      },
    });

    const { result } = renderWorkflow("d1");

    await waitFor(() => expect(result.current.cancelledJob).toEqual({ kind: "segment" }));
    // Settled: the bar is gone rather than left spinning on a terminal job.
    expect(result.current.watching).toBe(false);
    // And NOT an error - the reviewer asked for this.
    expect(result.current.banner).toBe("");
  });

  it("cancels the job the poller actually saw, by id", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "summarizing",
        active_job: {
          id: 42,
          kind: "summarize",
          state: "running",
          stage: "summarizing",
          current: 2,
          total: 9,
          error: null,
        },
      }),
    );
    mockStatus.mockResolvedValue({
      status: "summarizing",
      job: {
        id: 42,
        kind: "summarize",
        state: "running",
        stage: "summarizing",
        current: 2,
        total: 9,
        error: null,
      },
    });
    mockCancel.mockResolvedValue({
      id: 42,
      kind: "summarize",
      state: "running",
      stage: "summarizing",
      current: 2,
      total: 9,
      error: null,
      graceSeconds: 10,
    });

    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.watching).toBe(true));

    let grace = 0;
    await act(async () => {
      grace = await result.current.cancelActiveJob(false);
    });

    // By id, not "whatever is active": a document-scoped cancel could kill a job that started
    // between the render and the click.
    expect(mockCancel).toHaveBeenCalledWith("d1", 42, false);
    // The grace period comes from the server, so the Force stop moment cannot drift from the setting.
    expect(grace).toBe(10);
  });

  it("reports a failed stop instead of pretending the run ended", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "summarizing",
        active_job: {
          id: 9,
          kind: "summarize",
          state: "running",
          stage: "summarizing",
          current: 1,
          total: 4,
          error: null,
        },
      }),
    );
    mockStatus.mockResolvedValue({
      status: "summarizing",
      job: {
        id: 9,
        kind: "summarize",
        state: "running",
        stage: "summarizing",
        current: 1,
        total: 4,
        error: null,
      },
    });
    mockCancel.mockRejectedValue(new Error("network"));

    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.watching).toBe(true));
    await act(async () => {
      await result.current.cancelActiveJob(false);
    });

    expect(result.current.banner).toMatch(/could not stop/i);
  });

  it("restarts the cancelled kind through its own endpoint, and clears the prompt", async () => {
    mockDoc.mockResolvedValue(
      detail({
        status: "segmenting",
        active_job: {
          id: 3,
          kind: "segment",
          state: "running",
          stage: "segmenting",
          current: 0,
          total: 2,
          error: null,
        },
      }),
    );
    // Cancelled ONCE, then the restarted job. A cancelled job is not active, so /status reports the
    // NEW job afterwards - mocking `cancelled` forever would have the restart immediately re-settle
    // as cancelled, which cannot happen against the real endpoint.
    mockStatus
      .mockResolvedValueOnce({
        status: "uploaded",
        job: {
          id: 3,
          kind: "segment",
          state: "cancelled",
          stage: "cancelled",
          current: 0,
          total: 2,
          error: null,
        },
      })
      .mockResolvedValue({
        status: "segmenting",
        job: {
          id: 4,
          kind: "segment",
          state: "running",
          stage: "segmenting",
          current: 0,
          total: 2,
          error: null,
        },
      });
    mockStartSegment.mockResolvedValue({ ok: true });

    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.cancelledJob).not.toBeNull());

    await act(async () => {
      await result.current.restartCancelled(true); // Start over
    });

    expect(mockStartSegment).toHaveBeenCalledWith("d1", true);
    // The prompt clears immediately, so a second click cannot double-enqueue.
    expect(result.current.cancelledJob).toBeNull();
    expect(mockStartSummarize).not.toHaveBeenCalled();
  });
});

// reloadRows is how a Duplicates/Summaries action reaches the editor's in-memory buffer. It used to
// REPLACE that buffer, which discarded anything the reviewer had typed but not saved - and because
// both callers live on other tabs, the editor is unmounted and nothing on screen showed what went.
describe("useReviewWorkflow reloadRows", () => {
  const serverRow = (over: Record<string, unknown> = {}) => ({
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

  it("replaces the buffer wholesale when there is nothing unsaved to lose", async () => {
    // Unchanged behaviour, pinned: with a clean buffer the server is authoritative, so a re-segment
    // or a boundary change on another tab still lands in full.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    mockDoc.mockResolvedValue(
      detail({ status: "reviewing", rows: [serverRow({ start: 4, end: 8, title: "From server" })] }),
    );
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0].title).toBe("From server");
    expect([result.current.rows[0].start, result.current.rows[0].end]).toEqual([4, 8]);
  });

  it("keeps unsaved edits and still picks up the include the Duplicates tab wrote", async () => {
    // DEMONSTRATES the bug: on origin/main the reviewer's title is gone after this.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockRejectedValue(new Error("boom")); // the autosave fails -> saveState "error"
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() =>
      result.current.onRowsChange([
        { ...serverRow({ title: "Reviewer's title", date: "09/09/2026" }), _key: "k1" },
      ]),
    );
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));

    // Meanwhile "Keep this one" on the Duplicates tab excluded that row server-side.
    mockDoc.mockResolvedValue(
      detail({ status: "reviewing", rows: [serverRow({ include: false, title: "" })] }),
    );
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0].title).toBe("Reviewer's title"); // NOT discarded
    expect(result.current.rows[0].date).toBe("09/09/2026");
    expect(result.current.rows[0].include).toBe(false); // ...and the server's write still landed
  });

  it("does not send the stale local rows back to the server", async () => {
    // Flushing first would look like a fix and would overwrite the very write that triggered this
    // callback, which is the bug reloadRows exists to prevent.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockRejectedValue(new Error("boom"));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() => result.current.onRowsChange([{ ...serverRow({ title: "Local" }), _key: "k1" }]));
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));
    mockSave.mockClear();

    mockDoc.mockResolvedValue(detail({ status: "reviewing", rows: [serverRow({ include: false })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(mockSave).not.toHaveBeenCalled();
  });
});

// The residual Adrian measured on #176: `include` and `category` are BOTH server-written and
// locally editable, so taking them from the server unconditionally still reverted an unsaved edit
// to either. These reproduce his two cases end to end.
//
// They edit the rows the hook actually holds rather than inventing a row, because the protection is
// keyed on the client `_key`: a row whose key the buffer has never seen reads as newly inserted,
// not as an edit, and nothing would be tracked. That is also why the earlier reload tests pass with
// a made-up key - title and date need no protection, so they never exercised this path.
describe("useReviewWorkflow reloadRows protects unsaved edits to the server-written fields", () => {
  const serverRow = (over: Record<string, unknown> = {}) => ({
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

  /** Boot the editor, apply `edit` to the loaded row, and leave the save failing so nothing retries
   *  - the state in which the buffer holds the only copy of that edit. */
  async function withFailedSave(edit: Record<string, unknown>) {
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockRejectedValue(new Error("boom"));
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));
    act(() =>
      result.current.onRowsChange(result.current.rows.map((row) => ({ ...row, ...edit }))),
    );
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));
    return result;
  }

  it("keeps an unsaved re-classify when another tab re-classifies a different summary", async () => {
    const result = await withFailedSave({ category: "13" });

    // The Summaries tab wrote a DIFFERENT row's category, so this row is unchanged server-side.
    mockDoc.mockResolvedValue(detail({ status: "reviewing", rows: [serverRow({ category: "1" })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].category).toBe("13");
  });

  it("keeps an unsaved untick when 'Keep this one' fires on the Duplicates tab", async () => {
    const result = await withFailedSave({ include: false });

    mockDoc.mockResolvedValue(detail({ status: "reviewing", rows: [serverRow({ include: true })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].include).toBe(false);
  });

  it("still adopts the server's value for a field the reviewer has NOT touched", async () => {
    // The reload has to keep working, or protecting the edits would recreate the stale-buffer bug
    // this function exists to prevent.
    const result = await withFailedSave({ title: "Reviewer's title" });

    mockDoc.mockResolvedValue(
      detail({ status: "reviewing", rows: [serverRow({ include: false, category: "5" })] }),
    );
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].title).toBe("Reviewer's title");
    expect(result.current.rows[0].include).toBe(false);
    expect(result.current.rows[0].category).toBe("5");
  });

  it("stops protecting a field once the save that carried it has landed", async () => {
    // After a successful save the server holds the reviewer's value itself, so the row must track
    // the server again - otherwise one edit pins that field for the rest of the session.
    mockDoc.mockResolvedValue(detail({ status: "reviewing" }));
    mockSave.mockResolvedValue({ ok: true, count: 1 });
    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    act(() => result.current.onRowsChange(result.current.rows.map((r) => ({ ...r, category: "13" }))));
    await waitFor(() => expect(result.current.saveState.kind).toBe("saved"));

    // Back into an unsaved state, but via a DIFFERENT field.
    mockSave.mockRejectedValue(new Error("boom"));
    act(() => result.current.onRowsChange(result.current.rows.map((r) => ({ ...r, title: "t" }))));
    await waitFor(() => expect(result.current.saveState.kind).toBe("error"));

    mockDoc.mockResolvedValue(detail({ status: "reviewing", rows: [serverRow({ category: "5" })] }));
    await act(async () => {
      await result.current.reloadRows();
    });

    expect(result.current.rows[0].category).toBe("5"); // the save released the claim on it
    expect(result.current.rows[0].title).toBe("t"); // the still-unsaved title survives
  });
});


// #202. `get_status` now resolves the attention payload from the newest SUMMARIZE job while
// progress still describes the newest job of any kind, so after a dedup has run `job.error` is the
// dedup's (usually null) and only the payload still knows why the summarize stopped. The message
// must therefore come from the payload, so it and the row list are always the same run's.
describe("useReviewWorkflow recovers the needs_attention detail after a later job", () => {
  const failedRows = [{ idx: 2, pages: "5-6", reason: "unreadable" }];

  it("takes the message from the attention payload, not the newest job's error", async () => {
    mockDoc.mockResolvedValue(detail({ status: "needs_attention" }));
    mockStatus.mockResolvedValue({
      status: "needs_attention",
      job: {
        id: 9,
        kind: "dedup", // a dedup ran after the failed summarize
        state: "done",
        stage: "deduping",
        current: 1,
        total: 1,
        error: null, // the dedup's error - null, and NOT the reason the summarize stopped
        attention: { message: "Two documents could not be read.", rows: failedRows },
      },
    });

    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    expect(result.current.attention?.message).toBe("Two documents could not be read.");
    expect(result.current.attention?.rows).toHaveLength(1);
    expect(result.current.attention?.rows[0].idx).toBe(2);
  });

  it("keeps the generic notice when neither source says anything", async () => {
    mockDoc.mockResolvedValue(detail({ status: "needs_attention" }));
    mockStatus.mockResolvedValue({
      status: "needs_attention",
      job: {
        id: 9, kind: "dedup", state: "done", stage: "deduping", current: 1, total: 1,
        error: null,
        attention: null,
      },
    });

    const { result } = renderWorkflow("d1");
    await waitFor(() => expect(result.current.section).toBe("editor"));

    expect(result.current.attention?.message).toBe("Some documents need attention.");
    expect(result.current.attention?.rows).toEqual([]);
  });
});
