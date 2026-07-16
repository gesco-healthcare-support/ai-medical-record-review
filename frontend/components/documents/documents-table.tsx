"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, FileText } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { StatusPill } from "./status-pill";
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
  { key: "failed", label: "Failed", match: (d) => d.status === "error" || d.status === "interrupted" },
];

const SORT_ACCESSORS = {
  name: (d: DocumentListItem) => (d.original_filename || "").toLowerCase(),
  pages: (d: DocumentListItem) => d.page_count || 0,
  uploaded: (d: DocumentListItem) => d.created_at || "",
  found: (d: DocumentListItem) => d.rows_count || 0,
  activity: (d: DocumentListItem) => d.updated_at || "",
};
type SortKey = keyof typeof SORT_ACCESSORS;

const COLUMNS: { key: SortKey; label: string; cls: string }[] = [
  { key: "name", label: "Document", cls: "" },
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

export function DocumentsTable({
  docs,
  onOpen,
  onIdentify,
  onDelete,
}: {
  docs: DocumentListItem[];
  onOpen: (id: string) => void;
  onIdentify?: (doc: DocumentListItem) => void;
  onDelete?: (doc: DocumentListItem) => void;
}) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("uploaded");
  const [sortDir, setSortDir] = useState(-1);
  const [page, setPage] = useState(0);

  const visible = useMemo(() => {
    const active = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
    const query = search.trim().toLowerCase();
    const accessor = SORT_ACCESSORS[sortKey];
    return docs
      .filter(active.match)
      .filter((d) => !query || (d.original_filename || "").toLowerCase().includes(query))
      .sort((a, b) => {
        const [va, vb] = [accessor(a), accessor(b)];
        return (va < vb ? -1 : va > vb ? 1 : 0) * sortDir;
      });
  }, [docs, filter, search, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount - 1);
  const start = curPage * PAGE_SIZE;
  const pageDocs = visible.slice(start, start + PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => d * -1);
    } else {
      setSortKey(key);
      setSortDir(key === "name" ? 1 : -1);
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
                return (
                  <th key={col.key} className={cn(col.cls, active && "sorted")} aria-sort={active ? (sortDir === 1 ? "ascending" : "descending") : "none"}>
                    <button type="button" className="hd-sortbtn" onClick={() => toggleSort(col.key)}>
                      <span className="hd-sortlabel">
                        {col.label}
                        {active ? (
                          sortDir === 1 ? (
                            <ArrowUp width={11} height={11} aria-hidden />
                          ) : (
                            <ArrowDown width={11} height={11} aria-hidden />
                          )
                        ) : null}
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
                <td colSpan={7}>No documents match this view.</td>
              </tr>
            ) : (
              pageDocs.map((doc) => (
                <tr key={doc.id} onClick={() => onOpen(doc.id)}>
                  <td>
                    <span className="hd-doc">
                      <FileText width={15} height={15} aria-hidden />
                      <span className="hd-name">{doc.original_filename}</span>
                    </span>
                  </td>
                  <td className="hd-muted">{doc.page_count}</td>
                  <td className="hd-muted">{new Date(doc.created_at).toLocaleDateString()}</td>
                  <td className="hd-muted">{doc.rows_count || "—"}</td>
                  <td className="hd-muted">{relTime(doc.updated_at)}</td>
                  <td>
                    <StatusPill doc={doc} />
                  </td>
                  <td className="hd-menu-cell" onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger className="hd-dots" aria-label="Actions">
                        &#8943;
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-52">
                        <DropdownMenuItem onSelect={() => onOpen(doc.id)}>Open</DropdownMenuItem>
                        {onIdentify ? (
                          <DropdownMenuItem
                            disabled={Boolean(doc.active_job)}
                            onSelect={() => onIdentify(doc)}
                          >
                            {doc.rows_count ? "Re-run identification" : "Start identification"}
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
              ))
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
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={curPage === 0}
            >
              Prev
            </button>
            <button
              type="button"
              className="ev-btn ev-btn-outline ev-btn-sm"
              onClick={() => setPage((p) => p + 1)}
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
