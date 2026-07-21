"use client";

import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useDocuments } from "@/hooks/use-documents";
import {
  useCategories,
  useCreateCategory,
  useReprocess,
  useUpdateCategory,
} from "@/hooks/use-admin";
import { ApiError } from "@/lib/api";
import type { AdminCategory, CategoryInput } from "@/lib/admin-api";
import { cn } from "@/lib/utils";
import { CategoryDialog } from "./category-dialog";
import { PromptDialog } from "./prompt-dialog";

function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span className={cn("hd-badge", `hd-badge-${tone}`)}>
      <span className="hd-dot" aria-hidden />
      {children}
    </span>
  );
}

function errMessage(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback;
}

/** Admin console: the category catalog + per-category summary prompts, plus reprocessing a
 *  summarized record with the current prompts. is_admin gated (the API also 403s; this adds a
 *  friendly notice for a non-admin who deep-links). */
export function AdminView() {
  const { data: user, isLoading: userLoading } = useCurrentUser();
  const { data: categories = [], isLoading } = useCategories();
  const { data: docs = [] } = useDocuments();
  const create = useCreateCategory();
  const update = useUpdateCategory();
  const reprocess = useReprocess();

  const [catOpen, setCatOpen] = useState(false);
  const [editing, setEditing] = useState<AdminCategory | null>(null);
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptCat, setPromptCat] = useState<AdminCategory | null>(null);
  const [reprocessId, setReprocessId] = useState("");
  const [reprocessMsg, setReprocessMsg] = useState("");

  const summarized = docs.filter((d) => d.status === "done");

  if (!userLoading && user && !user.is_superuser) {
    return (
      <main>
        <section className="hd-column">
          <div className="hd-header">
            <div>
              <h1>Admin</h1>
              <p className="muted">You need an admin account to view this page.</p>
            </div>
          </div>
        </section>
      </main>
    );
  }

  async function toggleActive(cat: AdminCategory) {
    try {
      await update.mutateAsync({ id: cat.id, body: { active: !cat.active } });
    } catch (err) {
      toast.error(errMessage(err, "Could not update the category."));
    }
  }

  async function onCreate(body: CategoryInput & { id: string }) {
    await create.mutateAsync(body);
    toast.success("Category added.");
  }
  async function onUpdate(id: string, body: Partial<CategoryInput>) {
    await update.mutateAsync({ id, body });
    toast.success("Category saved.");
  }

  async function runReprocess() {
    if (!reprocessId) {
      setReprocessMsg("Choose a record to re-run.");
      return;
    }
    const name = summarized.find((d) => d.id === reprocessId)?.original_filename || "the record";
    setReprocessMsg("Re-running...");
    try {
      await reprocess.mutateAsync(reprocessId);
      setReprocessMsg(`Re-run started for ${name} - summaries update when it finishes.`);
      setReprocessId("");
    } catch (err) {
      setReprocessMsg(errMessage(err, "Could not reprocess that record."));
    }
  }

  return (
    <main>
      <section className="hd-column">
        <div className="hd-header">
          <div>
            <h1>Categories &amp; prompts</h1>
            <p className="muted">
              Add or edit the document categories and their summary prompts. Deactivating a category
              hides it from new work but keeps existing records intact.
            </p>
          </div>
          <button
            type="button"
            className="ev-btn ev-btn-primary ev-btn-lg"
            onClick={() => {
              setEditing(null);
              setCatOpen(true);
            }}
          >
            Add category
          </button>
        </div>

        <div className="hd-card">
          <table className="hd-table">
            <thead>
              <tr>
                <th className="hd-w-pages">ID</th>
                <th>Name &amp; description</th>
                <th>Examples</th>
                <th className="hd-w-found">Auto-assign</th>
                <th className="hd-w-status">Active</th>
                <th className="hd-w-status">Summary prompt</th>
                <th className="hd-w-menu" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {isLoading ? null : categories.length === 0 ? (
                <tr className="hd-norows">
                  <td colSpan={7}>No categories yet.</td>
                </tr>
              ) : (
                categories.map((cat) => (
                  <tr key={cat.id} className={cn(!cat.active && "admin-inactive")}>
                    <td className="hd-muted">{cat.id}</td>
                    <td>
                      <span className="hd-name">{cat.name}</span>
                      {cat.description ? <div className="admin-desc">{cat.description}</div> : null}
                    </td>
                    <td>
                      <span className="admin-examples">
                        {cat.examples.length ? cat.examples.join(" · ") : "—"}
                      </span>
                    </td>
                    <td className="hd-muted">{cat.auto_assign ? "Yes" : "No"}</td>
                    <td>
                      <Badge tone={cat.active ? "success" : "neutral"}>
                        {cat.active ? "Active" : "Inactive"}
                      </Badge>
                    </td>
                    <td>
                      <Badge tone={cat.has_summary_prompt ? "info" : "neutral"}>
                        {cat.has_summary_prompt ? "Custom" : "General"}
                      </Badge>
                    </td>
                    <td className="hd-menu-cell hd-admin-actions">
                      <button
                        type="button"
                        className="ev-btn ev-btn-outline ev-btn-sm"
                        onClick={() => {
                          setEditing(cat);
                          setCatOpen(true);
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-outline ev-btn-sm"
                        onClick={() => {
                          setPromptCat(cat);
                          setPromptOpen(true);
                        }}
                      >
                        Prompt
                      </button>
                      <button
                        type="button"
                        className="ev-btn ev-btn-ghost ev-btn-sm"
                        onClick={() => toggleActive(cat)}
                      >
                        {cat.active ? "Deactivate" : "Activate"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="hd-card mt-6 flex flex-col gap-3 p-6">
          <div>
            <h2 className="font-heading text-[20px] font-semibold tracking-[-0.01em]">
              Reprocess a record
            </h2>
            <p className="muted">
              Re-summarize a summarized record with the current prompts - useful after editing a
              category or prompt. This replaces any evaluator edits on that record&apos;s summaries.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2.5">
            <div className="grid min-w-[280px] flex-1 gap-1.5">
              <label className="ev-lbl" htmlFor="reprocessDoc">
                Summarized record
              </label>
              <span className="rc-selwrap">
                <select
                  id="reprocessDoc"
                  className="rc-sel"
                  value={reprocessId}
                  onChange={(e) => setReprocessId(e.target.value)}
                >
                  <option value="">
                    {summarized.length ? "Choose a record..." : "No summarized records yet"}
                  </option>
                  {summarized.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.original_filename}
                    </option>
                  ))}
                </select>
              </span>
            </div>
            <button
              type="button"
              className="ev-btn ev-btn-primary"
              onClick={runReprocess}
              disabled={reprocess.isPending || !reprocessId}
            >
              {reprocess.isPending ? "Re-running..." : "Re-run summaries"}
            </button>
          </div>
          <div className="muted min-h-[1.2em]">{reprocessMsg}</div>
        </div>
      </section>

      <CategoryDialog
        open={catOpen}
        onOpenChange={setCatOpen}
        editing={editing}
        onCreate={onCreate}
        onUpdate={onUpdate}
        saving={create.isPending || update.isPending}
      />
      <PromptDialog open={promptOpen} onOpenChange={setPromptOpen} category={promptCat} />
    </main>
  );
}
