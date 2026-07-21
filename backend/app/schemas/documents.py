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


class SummaryEditPayload(BaseModel):
    # All optional; the route uses model_dump(exclude_unset=True) so only fields the client
    # actually sent are written (mirrors the Flask `if field in body` semantics).
    summaryTitle: str | None = None
    summaryDate: str | None = None
    summaryText: str | None = None
    excluded: bool | None = None


class ResummarizePayload(BaseModel):
    model: str | None = None


class ExportPayload(BaseModel):
    patientName: str = ""
    patientdob: str = ""
    QMEorAME: str = ""
    lawfirm: str = ""


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
