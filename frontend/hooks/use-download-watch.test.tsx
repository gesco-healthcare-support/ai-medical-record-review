import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/download", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/download")>();
  return { ...actual, fetchDownloadStatus: vi.fn() };
});

import {
  COMPLETE,
  DOWNLOADING,
  EXPIRED,
  NOT_STARTED,
  POLL_MS,
  RECOVERED,
  useDownloadWatch,
  WATCH_LIMIT_MS,
} from "@/hooks/use-download-watch";
import { ApiError } from "@/lib/api";
import {
  DOWNLOAD_INTERRUPTED,
  type DownloadState,
  fetchDownloadStatus,
  type PreparedDownload,
} from "@/lib/download";

/** #390 PR 2: the page cannot see a download once the browser has it, so it asks the server every 2 s and
 *  says one sentence per state. Synthetic values only. */
const PREPARED: PreparedDownload = {
  token: "tok",
  url: "/api/documents/doc-1/downloads/tok",
  filename: "x.docx",
  size: 4,
};
const status = vi.mocked(fetchDownloadStatus);

function answers(...states: DownloadState[]) {
  for (const state of states) status.mockResolvedValueOnce({ state, size: 4 });
}

async function nextPoll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  status.mockReset();
});

// The #392 guard fails any file that leaves fake timers on.
afterEach(() => vi.useRealTimers());

describe("useDownloadWatch", () => {
  it("says Downloading... as soon as a download is handed over", () => {
    const { result } = renderHook(() => useDownloadWatch(PREPARED));
    expect(result.current).toEqual({ message: DOWNLOADING, tone: "info", watching: true });
  });

  it("says Download complete. when the server saw the whole file go, and stops asking", async () => {
    answers("downloading", "complete");
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await nextPoll();
    expect(result.current.message).toBe(DOWNLOADING);
    await nextPoll();
    expect(result.current).toEqual({ message: COMPLETE, tone: "ok", watching: false });
    await nextPoll();
    expect(status).toHaveBeenCalledTimes(2);
  });

  it("says interrupted, keeps watching, and says The download finished. when a resume completes it", async () => {
    answers("interrupted", "downloading", "complete");
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await nextPoll();
    expect(result.current).toEqual({ message: DOWNLOAD_INTERRUPTED, tone: "err", watching: true });
    await nextPoll();
    expect(result.current.message).toBe(DOWNLOADING);
    await nextPoll();
    expect(result.current).toEqual({ message: RECOVERED, tone: "ok", watching: false });
  });

  it("says the download has not started once 30 s pass with no request, and takes it back if it starts", async () => {
    status.mockResolvedValue({ state: "waiting", size: 4 });
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    for (let poll = 0; poll < 14; poll++) await nextPoll(); // 28 s
    expect(result.current.message).toBe(DOWNLOADING);
    await nextPoll(); // 30 s
    expect(result.current).toEqual({ message: NOT_STARTED, tone: "err", watching: true });

    status.mockResolvedValue({ state: "downloading", size: 4 });
    await nextPoll();
    expect(result.current.message).toBe(DOWNLOADING);
  });

  it("says the link expired when the download never started", async () => {
    answers("waiting", "expired");
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await nextPoll();
    await nextPoll();
    expect(result.current).toEqual({ message: EXPIRED, tone: "err", watching: false });
  });

  it("stops asking when the server no longer has the download's record", async () => {
    status.mockRejectedValueOnce(new ApiError("not found", 404));
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await nextPoll();
    expect(result.current.watching).toBe(false);
    await nextPoll();
    expect(status).toHaveBeenCalledTimes(1);
  });

  it("keeps its last sentence and asks again after a question that failed", async () => {
    status.mockRejectedValueOnce(new ApiError("network", 0));
    answers("complete");
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await nextPoll();
    expect(result.current).toEqual({ message: DOWNLOADING, tone: "info", watching: true });
    await nextPoll();
    expect(result.current.message).toBe(COMPLETE);
  });

  it("gives up after 15 minutes, keeping its last sentence", async () => {
    status.mockResolvedValue({ state: "downloading", size: 4 });
    const { result } = renderHook(() => useDownloadWatch(PREPARED));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WATCH_LIMIT_MS + POLL_MS);
    });
    expect(result.current).toEqual({ message: DOWNLOADING, tone: "info", watching: false });
    const asked = status.mock.calls.length;
    await nextPoll();
    expect(status.mock.calls.length).toBe(asked);
  });

  it("stops asking when the caller lets go of the download", async () => {
    status.mockResolvedValue({ state: "downloading", size: 4 });
    const { result, rerender } = renderHook(
      ({ prepared }: { prepared: PreparedDownload | null }) => useDownloadWatch(prepared),
      { initialProps: { prepared: PREPARED as PreparedDownload | null } },
    );

    await nextPoll();
    rerender({ prepared: null });
    expect(result.current).toEqual({ message: "", tone: "info", watching: false });
    const asked = status.mock.calls.length;
    await nextPoll();
    expect(status.mock.calls.length).toBe(asked);
  });
});
