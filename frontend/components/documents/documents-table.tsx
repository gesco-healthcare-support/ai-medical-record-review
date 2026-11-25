"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronRight, FileText } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { StatusPill } from "./status-pill";
import { deadlineNote, deadlineSortKey, deadlineTone, usDate } from "@/lib/deadlines";
import { downloadRawPdf } from "@/lib/documents-api";
import { cn } from "@/lib/utils";
import type { DocumentListItem } from "@/lib/types";

const PAGE_SIZE = 20;

const FILTERS: ReadonlyArray<{
  key: string;
  label: string;
  match: (d: DocumentListItem) => boolean;
}> = [
  { key: "all", label: "All", match: () => true },
  { key: "reviewing", label: "Ready for review", match: (d) => d.status === "reviewing" },
  { key: "running", label: "Running", match: (d) => Boolean(d.active_job) },
  { key: "done", label: "Summarized", match: (d) => d.status === "done" },
  { key: "uploaded", label: "Uploaded", match: (d) => d.status === "uploaded" },
  { key: "failed", label: "Failed", match: (d) => d.status === "error" },
  // `interrupted` is NOT a failure, and the backend says so in one place on purpose:
  // `worker/failures._STATE_OUTCOMES` maps it to `orphaned`, described there as "reaped after a
  // restart killed its worker - NOT a failure", and `is_failure` excludes it. #218 measured what
  // conflating them costs - a 27.4% "did not complete cleanly" rate whose largest single part was
  // twelve restart orphans from one day - and this chip was the last surface still giving that
  // naive reading, under a label that tells the reviewer their record broke.
  //
  // It is a routine event rather than an exotic one: a worker restart produces it, and every deploy
  // restarts the workers. The remedy is also different from a failure's - run it again, nothing is
  // wrong with the record - which is why it earns its own chip rather than a softer shared label.
  { key: "interrupted", label: "Interrupted", match: (d) => d.status === "interrupted" },
  // The reviewer's own mark, not the pipeline's state: "Summarized" says the summaries exist, not
  // that someone finished checking and downloaded the review (a reviewer asked for it, 2026-11-05).
  { key: "completed", label: "Completed", match: (d) => Boolean(d.completed_at) },
  { key: "open", label: "Not completed", match: (d) => !d.completed_at },
];

const SORT_ACCESSORS = {
  due: deadlineSortKey,
  name: (d: DocumentListItem) => (d.original_filename || "").toLowerCase(),
  patient: (d: DocumentListItem) => (d.patient_name || "").toLowerCase(),
  pages: (d: DocumentListItem) => d.page_count || 0,
  uploaded: (d: DocumentListItem) => d.created_at || "",
  found: (d: DocumentListItem) => d.rows_count || 0,
  activity: (d: DocumentListItem) => d.updated_at || "",
};
type SortKey = keyof typeof SORT_ACCESSORS;

const COLUMNS: { key: SortKey; label: string; cls: string }[] = [
  { key: "name", label: "Document", cls: "" },
  { key: "due", label: "Due", cls: "hd-w-due" },
  { key: "patient", label: "Patient", cls: "hd-w-patient" },
  { key: "pages", label: "Pages", cls: "hd-w-pages" },
  { key: "uploaded", label: "Uploaded", cls: "hd-w-uploaded" },
  { key: "found", label: "Documents found", cls: "hd-w-found" },
  { key: "activity", label: "Last activity", cls: "hd-w-activity" },
];

/** Relative "last activity" label (ported from doc-table.js). Client-only (uses Date.now()). */
function relTime(iso: string): string {
  if (!iso) return "—";
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (then.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  return then.toLocaleDateString(undefined, opts);
}

/** The Completed mark, like Outlook's colour category: an outlined bar before the file name, filled
 *  once the reviewer marks the record Completed. A button of its own, so a click marks the record
 *  rather than opening it. */
function CompletedMark({
  doc,
  onToggle,
}: Readonly<{ doc: DocumentListItem; onToggle: (doc: DocumentListItem) => void }>) {
  const completed = Boolean(doc.completed_at);
  const label = completed
    ? `Completed ${new Date(doc.completed_at as string).toLocaleDateString()} - click to clear`
    : "Mark as completed";
  return (
    <button
      type="button"
      className={cn("hd-mark", completed && "on")}
      aria-pressed={completed}
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(doc);
      }}
    />
  );
}

export function DocumentsTable({
  docs,
  onOpen,
  onIdentify,
  onAssign,
  onDelete,
  onToggleCompleted,
  onBillingSheet,
  onDownloadRaw,
}: Readonly<{
  docs: DocumentListItem[];
  onOpen: (id: string) => void;
  onIdentify?: (doc: DocumentListItem) => void;
  /** Set or clear the reviewer's Completed mark - the bar before the file name, or the menu. */
  onToggleCompleted?: (doc: DocumentListItem) => void;
  /** Admins only: move the record to another reviewer. Off while a job runs, as on the server. */
  onAssign?: (doc: DocumentListItem) => void;
  onDelete?: (doc: DocumentListItem) => void;
  /** Open the office's billing sheet - offered for a record put together from received files. */
  onBillingSheet?: (doc: DocumentListItem) => void;
  /** Download the record's PDF as received - for a record put together from received files,
   *  with its billing sheet on top. Without it the menu saves the file at once. */
  onDownloadRaw?: (doc: DocumentListItem) => void;
}>) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  // Closest deadline first (lib/deadlines.ts): what to work on next is at the top.
  const [sortKey, setSortKey] = useState<SortKey>("due");
  const [sortDir, setSortDir] = useState(1);
  const [page, setPage] = useState(0);
  // One "today" per render, so every row counts its days from the same day.
  const today = new Date();

  const visible = useMemo(() => {
    const active = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
    const query = search.trim().toLowerCase();
    const accessor = SORT_ACCESSORS[sortKey];
    return docs
      .filter(active.match)
      .filter(
        (d) =>
          !query ||
          (d.original_filename || "").toLowerCase().includes(query) ||
          (d.patient_name || "").toLowerCase().includes(query),
      )
      .sort((a, b) => {
        const [va, vb] = [accessor(a), accessor(b)];
        if (va < vb) return -sortDir;
        if (va > vb) return sortDir;
        // Records due the same day - or with no deadline at all - newest upload first.
        return sortKey === "due" ? (b.created_at || "").localeCompare(a.created_at || "") : 0;
      });
  }, [docs, filter, search, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  // `page` is what the buttons set; `curPage` is what is SHOWN, clamped in case the list shrank
  // underneath it (deleting the last row of the last page, or a poll returning fewer records). The
  // two then disagree, and Prev/Next must move from the page on screen rather than from the stale
  // one - stepping back from `page` lands on the page already displayed and the click does nothing.
  const curPage = Math.min(page, pageCount - 1);
  const start = curPage * PAGE_SIZE;
  const pageDocs = visible.slice(start, start + PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => d * -1);
    } else {
      setSortKey(key);
      setSortDir(key === "name" || key === "due" ? 1 : -1);
    }
    setPage(0);
  }

  return (
    <>
      <div className="hd-toolbar">
        <div className="hd-chips">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={cn("hd-chip", filter === f.key && "active")}
              onClick={() => {
                setFilter(f.key);
                setPage(0);
              }}
            >
              {f.label} · {docs.filter(f.match).length}
            </button>
          ))}
        </div>
        <input
          className="ev-inp hd-search"
          type="search"
          placeholder="Search by filename..."
          aria-label="Search by filename"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
      </div>

      <div className="hd-card">
        <table className="hd-table">
          <thead>
            <tr>
              {COLUMNS.map((col) => {
                const active = sortKey === col.key;
                const direction = sortDir === 1 ? "ascending" : "descending";
                const arrow =
                  sortDir === 1 ? (
                    <ArrowUp width={11} height={11} aria-hidden />
                  ) : (
                    <ArrowDown width={11} height={11} aria-hidden />
                  );
                return (
                  <th key={col.key} className={cn(col.cls, active && "sorted")} aria-sort={active ? direction : "none"}>
                    <button type="button" className="hd-sortbtn" onClick={() => toggleSort(col.key)}>
                      <span className="hd-sortlabel">
                        {col.label}
                        {active ? arrow : null}
                      </span>
                    </button>
                  </th>
                );
              })}
              <th className="hd-w-status">Status</th>
              <th className="hd-w-menu" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {pageDocs.length === 0 ? (
              <tr className="hd-norows">
                <td colSpan={9}>No documents match this view.</td>
              </tr>
            ) : (
              pageDocs.map((doc) => {
                const tone = deadlineTone(doc);
                return (
                  <tr
                    key={doc.id}
                    data-id={doc.id}
                    data-deadline={tone ?? undefined}
                    className={tone ? `hd-due-${tone}` : undefined}
                    onClick={() => onOpen(doc.id)}
                  >
                    <td>
                      <span className="hd-doc">
                        {onToggleCompleted ? (
                          <CompletedMark doc={doc} onToggle={onToggleCompleted} />
                        ) : null}
                        <FileText width={15} height={15} aria-hidden />
                        <span className="hd-name">{doc.original_filename}</span>
                        <ChevronRight width={15} height={15} aria-hidden className="hd-open-cue" />
                      </span>
                    </td>
                    <td>
                      {doc.due_on ? (
                        <>
                          <span className="hd-due-date">{usDate(doc.due_on)}</span>
                          <span className="hd-due-note">{deadlineNote(doc, today)}</span>
                        </>
                      ) : (
                        <span className="hd-muted">—</span>
                      )}
                    </td>
                    <td className="hd-muted">{doc.patient_name || "—"}</td>
                    <td className="hd-muted">{doc.page_count}</td>
                    <td className="hd-muted">{new Date(doc.created_at).toLocaleDateString()}</td>
                    <td className="hd-muted">{doc.rows_count || "—"}</td>
                    <td className="hd-muted">{relTime(doc.updated_at)}</td>
                    <td>
                      <StatusPill doc={doc} />
                      {doc.downloaded_at ? (
                        <span className="hd-downloaded">
                          Downloaded {new Date(doc.downloaded_at).toLocaleDateString()}
                        </span>
                      ) : null}
                    </td>
                    <td className="hd-menu-cell" onClick={(e) => e.stopPropagation()}>
                      <DropdownMenu>
                        <DropdownMenuTrigger className="hd-dots" aria-label="Actions">
                          &#8943;
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-52">
                          <DropdownMenuItem onSelect={() => onOpen(doc.id)}>Open</DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() =>
                              onDownloadRaw ? onDownloadRaw(doc) : downloadRawPdf(doc.id)
                            }
                          >
                            Download raw PDF
                          </DropdownMenuItem>
                          {onBillingSheet && doc.received_on ? (
                            <DropdownMenuItem onSelect={() => onBillingSheet(doc)}>
                              Billing sheet
                            </DropdownMenuItem>
                          ) : null}
                          {onToggleCompleted ? (
                            <DropdownMenuItem onSelect={() => onToggleCompleted(doc)}>
                              {doc.completed_at ? "Clear Completed" : "Mark as completed"}
                            </DropdownMenuItem>
                          ) : null}
                          {onIdentify ? (
                            <DropdownMenuItem
                              disabled={Boolean(doc.active_job)}
                              onSelect={() => onIdentify(doc)}
                            >
                              {doc.rows_count ? "Re-run identification" : "Start identification"}
                            </DropdownMenuItem>
                          ) : null}
                          {onAssign ? (
                            <DropdownMenuItem
                              disabled={Boolean(doc.active_job)}
                              onSelect={() => onAssign(doc)}
                            >
                              Assign to...
                            </DropdownMenuItem>
                          ) : null}
                          {onDelete ? (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                                onSelect={() => onDelete(doc)}
                              >
                                Delete...
                              </DropdownMenuItem>
                            </>
                          ) : null}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
        <div className="hd-foot">
          <span>
            {visible.length
              ? `${start + 1}-${Math.min(start + PAGE_SIZE, visible.length)} of ${visible.length}`
              : "0 of 0"}
          </span>
          <div className="hd-foot-nav">
            <button
              type="button"
              className="ev-btn ev-btn-outline ev-btn-sm"
              onClick={() => setPage(Math.max(0, curPage - 1))}
              disabled={curPage === 0}
            >
              Prev
            </button>
            <button
              type="button"
              className="ev-btn ev-btn-outline ev-btn-sm"
              onClick={() => setPage(curPage + 1)}
              disabled={curPage >= pageCount - 1}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
