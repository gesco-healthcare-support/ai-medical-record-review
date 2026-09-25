"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  DOWNLOAD_INTERRUPTED,
  type DownloadState,
  fetchDownloadStatus,
  type PreparedDownload,
} from "@/lib/download";

/**
 * Watch a download the page has handed to the browser, through the server (#390 PR 2).
 *
 * Once the link is clicked the browser's own download manager owns the transfer and the page cannot see it,
 * so this asks the server every 2 s what its measured GET saw, and turns that into one sentence. It keeps
 * watching an interrupted download, because Chrome's Resume may complete it - and then says so, rather than
 * leaving a failure on screen that recovered. It stops at a final state, after 15 minutes, when the server no
 * longer has the record (404), or when the caller passes `null` or unmounts.
 *
 * KNOWN AND ACCEPTED (2026-09-25): COMPLETE means the server handed over the last byte, which can run a few
 * megabytes ahead of what the browser has received (see `delivery_status` in backend/app/services/downloads.py).
 */

export const DOWNLOADING = "Downloading...";
export const NOT_STARTED =
  "The download has not started. If the browser asked whether to allow downloads, allow it, then try again.";
export const EXPIRED = "The download did not start before its link expired. Please try again.";
export const RECOVERED = "The download finished.";
export const COMPLETE = "Download complete.";

export const POLL_MS = 2000;
export const NOT_STARTED_AFTER_MS = 30_000;
export const WATCH_LIMIT_MS = 15 * 60_000;

export type DownloadWatch = {
  /** The sentence to show, or "" before anything has been handed over. */
  message: string;
  /** How to show it: in progress, finished, or a problem. */
  tone: "info" | "ok" | "err";
  /** True while the page is still asking; the caller keeps its export buttons disabled meanwhile. */
  watching: boolean;
};

const IDLE: DownloadWatch = { message: "", tone: "info", watching: false };

/** The sentence for one answer from the server, and whether watching can stop. */
function viewFor(
  state: DownloadState | undefined,
  elapsedMs: number,
  sawInterruption: boolean,
): { view: DownloadWatch; done: boolean } {
  switch (state) {
    case "complete":
      return {
        view: { message: sawInterruption ? RECOVERED : COMPLETE, tone: "ok", watching: false },
        done: true,
      };
    case "expired":
      return { view: { message: EXPIRED, tone: "err", watching: false }, done: true };
    case "interrupted":
      return { view: { message: DOWNLOAD_INTERRUPTED, tone: "err", watching: true }, done: false };
    case "waiting":
      if (elapsedMs >= NOT_STARTED_AFTER_MS) {
        return { view: { message: NOT_STARTED, tone: "err", watching: true }, done: false };
      }
      return { view: { message: DOWNLOADING, tone: "info", watching: true }, done: false };
    default:
      return { view: { message: DOWNLOADING, tone: "info", watching: true }, done: false };
  }
}

export function useDownloadWatch(prepared: PreparedDownload | null): DownloadWatch {
  const [watch, setWatch] = useState<DownloadWatch>(IDLE);

  useEffect(() => {
    if (!prepared) {
      setWatch(IDLE);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawInterruption = false;
    const startedAt = Date.now();
    setWatch({ message: DOWNLOADING, tone: "info", watching: true });

    const stop = () => setWatch((current) => ({ ...current, watching: false }));

    async function check() {
      let answer: { view: DownloadWatch; done: boolean } | null = null;
      try {
        const status = await fetchDownloadStatus(prepared as PreparedDownload);
        if (cancelled) return;
        answer = viewFor(status.state, Date.now() - startedAt, sawInterruption);
        sawInterruption ||= status.state === "interrupted";
      } catch (err) {
        if (cancelled) return;
        // The record is gone (it outlived its 15 minutes, or never existed): nothing more to learn.
        if (err instanceof ApiError && err.status === 404) return stop();
        // Anything else is a failed question, not an answer: keep the last sentence and ask again.
      }
      if (answer) setWatch(answer.view);
      if (answer?.done) return;
      if (Date.now() - startedAt >= WATCH_LIMIT_MS) return stop();
      timer = setTimeout(check, POLL_MS);
    }

    timer = setTimeout(check, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [prepared]);

  return watch;
}
