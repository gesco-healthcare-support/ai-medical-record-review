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
    model: str | None = None
    patientName: str = ""
    patientdob: str = ""
    QMEorAME: str = ""
    lawfirm: str = ""


class HeaderPayload(BaseModel):
    """Reviewer-edited report header (PUT /documents/{id}/header)."""

    patient_first_name: str = ""
    patient_last_name: str = ""
    patient_dob: str = ""
    law_firm: str = ""


class DuplicateResolvePayload(BaseModel):
    """Resolve one duplicate cluster (POST /documents/{id}/duplicates/{group}/resolve).

    action="keep_one" keeps `primary_idx` and excludes the other members; action="dismiss" marks the
    whole cluster as not-duplicates; action="remove_member" drops the single row `idx` out of the
    cluster, for the mixed cluster where some copies are real and others are not. The route validates
    action + the referenced idx (-> 400)."""

    action: str  # "keep_one" | "dismiss" | "remove_member"
    primary_idx: int | None = None
    idx: int | None = None  # remove_member: the member to drop
