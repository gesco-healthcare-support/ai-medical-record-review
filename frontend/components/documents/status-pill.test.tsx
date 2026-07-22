import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill } from "@/components/documents/status-pill";
import type { DocumentListItem, JobProgress } from "@/lib/types";

const doc = (over: Partial<DocumentListItem>): DocumentListItem => ({
  id: "1",
  original_filename: "f.pdf",
  page_count: 1,
  status: "uploaded",
  created_at: "",
  updated_at: "",
  active_job: null,
  rows_count: 0,
  patient_first_name: "",
  patient_last_name: "",
  patient_name: "",
  patient_dob: "",
  law_firm: "",
  ...over,
});

// Source of truth: the labels + tones ported verbatim from doc-table.js into status-pill.tsx.
const CASES: Array<[DocumentListItem["status"], string, string]> = [
  ["uploaded", "Uploaded", "hd-badge-neutral"],
  ["segmenting", "Identifying documents", "hd-badge-info"],
  ["reviewing", "Ready for review", "hd-badge-warning"],
  ["summarizing", "Summarizing", "hd-badge-info"],
  ["done", "Summarized", "hd-badge-success"],
  ["needs_attention", "Needs attention", "hd-badge-warning"],
  ["error", "Failed", "hd-badge-danger"],
  ["interrupted", "Interrupted", "hd-badge-danger"],
];

describe("StatusPill", () => {
  it.each(CASES)("renders %s with its label and tone", (status, label, tone) => {
    const { container } = render(<StatusPill doc={doc({ status })} />);
    const badge = container.querySelector(".hd-badge");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveClass("hd-badge", tone);
  });

  it("appends the (current/total) progress suffix while a job runs", () => {
    const job: JobProgress = {
      kind: "segment",
      state: "running",
      stage: "segmenting",
      current: 2,
      total: 10,
      error: null,
    };
    const { container } = render(<StatusPill doc={doc({ status: "segmenting", active_job: job })} />);
    expect(container.querySelector(".hd-badge")).toHaveTextContent("Identifying documents (2/10)");
  });

  it("omits the suffix when the running job has no total yet", () => {
    const job: JobProgress = {
      kind: "segment",
      state: "running",
      stage: "segmenting",
      current: 0,
      total: 0,
      error: null,
    };
    const { container } = render(<StatusPill doc={doc({ status: "segmenting", active_job: job })} />);
    expect(container.querySelector(".hd-badge")?.textContent).not.toContain("(");
  });
});
