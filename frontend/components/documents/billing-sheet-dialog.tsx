"use client";

import { useState } from "react";
import { toast } from "sonner";
import { BillingFields } from "@/components/documents/billing-fields";
import { DIALOG_BODY, DIALOG_FRAME } from "@/components/documents/dialog-layout";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useBillingSheet, useSaveBillingSheet } from "@/hooks/use-documents";
import {
  type BillingSheet,
  type BillingSheetEdit,
  canDownload,
  editValid,
  isPageCount,
  shownCounts,
  toEdit,
} from "@/lib/billing-sheet";
import { downloadBillingSheet } from "@/lib/documents-api";
import { humanizeError } from "@/lib/errors";
import { RECORDS_FROM, RECORDS_FROM_LABELS } from "@/lib/received-files";
import type { DocumentListItem } from "@/lib/types";

const NOT_FOUND = "This record no longer exists. Refresh the list.";

function same(a: BillingSheetEdit, b: BillingSheetEdit): boolean {
  return (Object.keys(a) as (keyof BillingSheetEdit)[]).every((key) => a[key] === b[key]);
}

/** Where the counts stand: still being read, or what was worked out from the record. */
function CountsLine({ sheet }: Readonly<{ sheet: BillingSheet }>) {
  if (sheet.counted === null) {
    const read = sheet.pages_counted - sheet.pages_unread;
    return (
      <output className="banner-info block text-sm">
        Reading the record&apos;s pages: {read} of {sheet.pages_counted} read. The office count and
        the deposition pages are worked out once every page is read.
      </output>
    );
  }
  return (
    <output className="block text-sm text-muted-foreground">
      Worked out from the record: office count {sheet.counted.office}, deposition pages{" "}
      {sheet.counted.depo}. Type a count only to change it.
    </output>
  );
}

/** A note when the declared page count and the office count differ, so the reason gets typed. */
function Discrepancy({ sheet, edit }: Readonly<{ sheet: BillingSheet; edit: BillingSheetEdit }>) {
  const declared = edit.declared_pages.trim();
  const office = edit.office_count.trim() || String(shownCounts(sheet).office ?? "");
  if (!declared || !office || !isPageCount(declared) || declared === office) return null;
  return (
    <p className="notice-attention text-sm">
      The declaration states {declared} pages; the office count is {office}. Say why in the reason.
    </p>
  );
}

function BillingForm({
  id,
  sheet,
  onClose,
}: Readonly<{ id: string; sheet: BillingSheet; onClose: () => void }>) {
  const save = useSaveBillingSheet(id);
  const [edit, setEdit] = useState<BillingSheetEdit>(() => toEdit(sheet));
  const change = (patch: Partial<BillingSheetEdit>) => setEdit((prev) => ({ ...prev, ...patch }));
  const dirty = !same(edit, toEdit(sheet));
  const valid = editValid(edit);
  const downloadable = !dirty && canDownload(sheet);

  async function submit() {
    try {
      const saved = await save.mutateAsync(edit);
      setEdit(toEdit(saved));
      toast.success("Billing sheet saved.");
    } catch (err) {
      toast.error(humanizeError(err, { fallback: "Could not save the billing sheet.", notFound: NOT_FOUND }));
    }
  }

  let downloadHint: string | undefined;
  if (dirty) downloadHint = "Save the changes first.";
  else if (!canDownload(sheet)) downloadHint = "Ready once every page is read.";

  return (
    <>
      <div className={DIALOG_BODY}>
        <CountsLine sheet={sheet} />
        <div className="grid gap-3 sm:grid-cols-2">
          <BillingFields value={edit} onChange={change} doctors={sheet.doctors} idPrefix="billing" />
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="billing-from">
              Records from
            </label>
            <select
              id="billing-from"
              className="ev-inp"
              value={edit.records_from}
              onChange={(e) => change({ records_from: e.target.value })}
            >
              <option value="">Not chosen</option>
              {RECORDS_FROM.map((party) => (
                <option key={party} value={party}>
                  {RECORDS_FROM_LABELS[party]}
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="billing-office">
              Office count
            </label>
            <input
              id="billing-office"
              className="ev-inp"
              inputMode="numeric"
              autoComplete="off"
              placeholder={sheet.counted ? String(sheet.counted.office) : "Worked out once read"}
              value={edit.office_count}
              onChange={(e) => change({ office_count: e.target.value })}
            />
          </div>
          <div className="grid gap-1.5">
            <label className="ev-lbl" htmlFor="billing-depo">
              Deposition pages
            </label>
            <input
              id="billing-depo"
              className="ev-inp"
              inputMode="numeric"
              autoComplete="off"
              placeholder={sheet.counted ? String(sheet.counted.depo) : "Worked out once read"}
              value={edit.depo_pages}
              onChange={(e) => change({ depo_pages: e.target.value })}
            />
          </div>
        </div>
        <div className="grid gap-1.5">
          <label className="ev-lbl" htmlFor="billing-reason">
            Reason for discrepancy
          </label>
          <input
            id="billing-reason"
            className="ev-inp"
            autoComplete="off"
            value={edit.discrepancy_reason}
            onChange={(e) => change({ discrepancy_reason: e.target.value })}
          />
        </div>
        <Discrepancy sheet={sheet} edit={edit} />
        {valid ? null : (
          <output className="rc-hb-warn">
            Counts are whole numbers, and the claim numbers, the location and the reason at most
            255 characters.
          </output>
        )}
      </div>
      <DialogFooter className="shrink-0">
        <button type="button" className="ev-btn ev-btn-outline" onClick={onClose}>
          Done
        </button>
        <button
          type="button"
          className="ev-btn ev-btn-outline"
          title={downloadHint}
          disabled={!downloadable}
          onClick={() => downloadBillingSheet(id)}
        >
          Download PDF
        </button>
        <button
          type="button"
          className="ev-btn ev-btn-primary"
          disabled={!dirty || !valid || save.isPending}
          onClick={submit}
        >
          {save.isPending ? "Saving..." : "Save"}
        </button>
      </DialogFooter>
    </>
  );
}

/** The office's billing sheet for a record put together from received files (GET/PUT
 *  /api/documents/{id}/billing): what the uploader typed, editable, and the office count and the
 *  deposition pages worked out from the record once its pages are read - read again every few
 *  seconds until then. A count typed here replaces the worked-out one; a blank goes back to it.
 *  Download PDF gives the one-page sheet once both counts are known and nothing is left unsaved. */
export function BillingSheetDialog({
  doc,
  onClose,
}: Readonly<{ doc: DocumentListItem | null; onClose: () => void }>) {
  const sheet = useBillingSheet(doc?.id ?? null);
  let body = <p className="text-sm text-muted-foreground">Loading...</p>;
  if (doc && sheet.data) {
    body = <BillingForm key={doc.id} id={doc.id} sheet={sheet.data} onClose={onClose} />;
  } else if (sheet.isError) {
    body = (
      <p className="banner text-sm">
        {humanizeError(sheet.error, {
          fallback: "Could not read the billing sheet.",
          notFound: NOT_FOUND,
        })}
      </p>
    );
  }
  return (
    <Dialog
      open={doc !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className={DIALOG_FRAME}>
        <DialogHeader className="shrink-0">
          <DialogTitle>Billing sheet</DialogTitle>
          <DialogDescription>
            The office&apos;s billing sheet for this record. Change anything typed at upload, and
            say why the declared page count and the office count differ.
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  );
}
