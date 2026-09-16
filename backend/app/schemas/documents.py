"""Request bodies for the /api/documents router.

Row payloads stay loose dicts on purpose: `services.rows.validate_rows` is the single source of
truth for row validation and returns the exact per-row 400 error strings the client expects, so a
strict per-field schema here would pre-empt it with a different (422) contract.
"""

from typing import Any

from pydantic import BaseModel


class RowsPayload(BaseModel):
    rows: list[dict[str, Any]] = []


class SummarizeStartPayload(BaseModel):
    rows: list[dict[str, Any]] | None = None  # optional: flush the editor's final rows first
    model: str | None = None
    # "Re-summarize all": clear existing summaries first so every row is regenerated (discards
    # reviewer edits). Default false -> the resumable worker reuses done rows by identity (item 7).
    fresh: bool = False
    # Proceed even though no CURRENT duplicate check covers these rows (#125). The gate is soft on
    # purpose: a reviewer may reasonably skip the check on a short record, but skipping has to be a
    # decision rather than an omission, so this is explicit and the server audits it.
    skip_duplicate_check: bool = False


class SummaryEditPayload(BaseModel):
    # All optional; the route uses model_dump(exclude_unset=True) so only fields the client
    # actually sent are written (mirrors the Flask `if field in body` semantics).
    summaryTitle: str | None = None
    summaryDate: str | None = None
    summaryText: str | None = None
    excluded: bool | None = None
    # Re-classify this sub-document. Unlike the fields above it does NOT land on the Summary: it is
    # written through to the owning ReviewRow, so this edit and the same edit on Review & correct are
    # the same edit and cannot diverge. The next re-draft picks up the new category's prompt on its
    # own, because resummarize already resolves the prompt from the row.
    category: str | None = None


class CancelPayload(BaseModel):
    # `force` escalates from the cooperative stop to RQ's send_stop_job_command, which kills the
    # work-horse outright and leaves orphan recovery to reap the row. It is the second press of the
    # button, never the first: a hard kill can land between a delete and its re-insert, which is
    # exactly the state the cooperative path is designed to avoid.
    force: bool = False


class DedupStartPayload(BaseModel):
    # `fresh` re-OCRs from scratch by clearing each row's stored source_text. The default reuses it,
    # which is the same "continue" the duplicate check has always done implicitly.
    fresh: bool = False


class SegmentStartPayload(BaseModel):
    # `fresh` discards the segmentation checkpoints so every window is recomputed. The default
    # continues from whatever completed windows survive a previous cancel or timeout.
    fresh: bool = False


class ResummarizePayload(BaseModel):
    model: str | None = None


class ExportPayload(BaseModel):
    patientName: str = ""
    patientdob: str = ""
    QMEorAME: str = ""
    lawfirm: str = ""
    # Per-record "(Pages X-Y)" suffixes are an internal reviewing aid, so the presentable export is
    # what a caller gets by default; the export dialog opts in.
    includePageNumbers: bool = False


class BundlePayload(BaseModel):
    categories: list[Any] = []  # non-empty check lives in the route (-> 400), matching Flask
    label: str | None = None
    coverHeading: str | None = None  # see ZipBundle
    # What the download calls itself after the patient name, e.g. "List of Diagnostic and
    # Operative Reports". Optional: a client that predates it falls back to the old
    # slug-only filename rather than failing, which is the contract every field here has.
    downloadName: str | None = None
    model: str | None = None
    patientName: str = ""
    patientdob: str = ""
    QMEorAME: str = ""
    lawfirm: str = ""


class HeaderPayload(BaseModel):
    """Reviewer-edited report header (PUT /documents/{id}/header).

    Every field defaults to empty, so a client that predates one of them clears it rather
    than failing - the same contract the original four already had.

    `pages_received` is a STRING on the wire even though the column is an integer. The form
    sends what was typed, an empty box has to mean 'unset' rather than zero, and rejecting a
    typo with a 422 would lose the rest of the header the reviewer had just filled in. The
    route coerces; anything unparseable clears the field."""

    patient_first_name: str = ""
    patient_last_name: str = ""
    patient_dob: str = ""
    law_firm: str = ""
    attorney_name: str = ""
    doctor: str = ""
    letter_type: str = ""
    letter_date: str = ""
    pages_received: str = ""


class ZipBundle(BaseModel):
    """One category bundle to include in the export zip.

    The category lists live in the FRONTEND (`lib/bundles.ts`), which is where both bundle
    pages already read them from, so the zip asks for them rather than keeping a second
    copy of the taxonomy on this side."""

    label: str | None = None
    categories: list[Any] = []
    # The heading of the list page that goes in FRONT of the documents, e.g. "LIST OF
    # DIAGNOSTIC AND OPERATIVE REPORTS". Absent means no cover page, which is what the
    # depositions bundle sends - the reviewers asked for one on diagnostics only.
    coverHeading: str | None = None
    # See BundlePayload. Carried here too so a member of the archive is named exactly as its
    # standalone download is - three of the four members already carried the patient name and
    # the bundle did not.
    downloadName: str | None = None


class ExportZipPayload(ExportPayload):
    """POST /documents/{id}/export/zip - the export-dialog fields plus the bundles to add.

    A bundle that matches nothing in this record is OMITTED from the zip rather than
    failing the request, which is the one behaviour that differs from /bundle/pdf: a record
    with no depositions still has an MRR and a linked PDF worth downloading."""

    bundles: list[ZipBundle] = []


class DuplicateResolvePayload(BaseModel):
    """Resolve one duplicate cluster (POST /documents/{id}/duplicates/{group}/resolve).

    action="keep_one" keeps `primary_idx` and excludes the other members; action="dismiss" marks the
    whole cluster as not-duplicates; action="remove_member" drops the single row `idx` out of the
    cluster, for the mixed cluster where some copies are real and others are not. The route validates
    action + the referenced idx (-> 400)."""

    action: str  # "keep_one" | "dismiss" | "remove_member"
    primary_idx: int | None = None
    idx: int | None = None  # remove_member: the member to drop
