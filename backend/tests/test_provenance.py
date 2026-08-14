"""Per-call-type model tiering and prompt provenance.

Two things are pinned here:

* The three summarize calls go to the three models the JOB was created with - body on 2.5-pro, title
  and audit on flash by default - and a job created before this change (NULL columns) still behaves
  exactly as it did.
* A summary records the prompt text that produced it as a fingerprint over the RESOLVED prompt, so a
  DB-side admin edit moves it. That is the case a hash over prompts.py alone would miss.

Pure: `_generate` and `verify_summary` are monkeypatched, so nothing hits Vertex. The fingerprint
tests need no database either - `summary_prompt_fingerprint` is called with explicit strings.
"""

from app.services import summarize_engine as se
from app.services.provenance import (
    fingerprint,
    summary_prompt_fingerprint,
)

_NO_ISSUES = {"fixed_text": "", "issues": [], "ok": True}  # the audit RAN and found nothing


def _row(**over):
    row = {
        "start": 1,
        "end": 2,
        "category": "1",
        "date": "2026-01-01",
        "injury_date": "-",
        "flag": "",
    }
    row.update(over)
    return row


def _capture_models(monkeypatch):
    """Run summarize_row with the model calls stubbed; return the list of models it addressed."""
    seen: list[tuple[str, str]] = []

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        kind = "title" if system_msg == se.TITLE_PROMPT else "body"
        seen.append((kind, model))
        return ("Progress Note" if kind == "title" else "Summary body"), False

    def fake_verify(model, source, summary, title=None, document_date=None):
        seen.append(("audit", model))
        return {"fixed_text": summary, "fixed_title": title, "issues": []}

    monkeypatch.setattr(
        se,
        "extract_text_from_selected_pages",
        # **_kw absorbs page_label_offset, which the deposition work added to the real
        # signature. This stub pins BEHAVIOUR, not an exact call signature.
        lambda path, pages, mark_pages=False, **_kw: "raw OCR",
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", fake_verify)
    return seen


# --- model tiering -------------------------------------------------------------------------------


def test_the_three_calls_go_to_the_three_models_they_were_given(monkeypatch):
    # WHEN a row is summarized, THE SYSTEM SHALL send the body to `model`, the title to
    # `title_model` and the audit to `audit_model`. Before 2026-08-06 all three went to `model`.
    seen = _capture_models(monkeypatch)
    se.summarize_row(
        "/x.pdf",
        _row(),
        model="body-m",
        prompt="P",
        verify=True,
        title_model="title-m",
        audit_model="audit-m",
    )
    assert dict(seen) == {"body": "body-m", "title": "title-m", "audit": "audit-m"}


def test_omitted_models_fall_back_to_config_not_to_the_body_model(monkeypatch):
    # WHEN a standalone caller omits the per-call models, THE SYSTEM SHALL resolve them from config -
    # which on the Gemini path is flash for title and audit, NOT the body model.
    seen = _capture_models(monkeypatch)
    se.summarize_row("/x.pdf", _row(), model="body-m", prompt="P", verify=True)
    by_kind = dict(seen)
    assert by_kind["body"] == "body-m"
    assert by_kind["title"] == "gemini-2.5-flash"
    assert by_kind["audit"] == "gemini-2.5-flash"


def test_the_output_records_which_model_wrote_each_part(monkeypatch):
    # WHEN a summary is produced, THE SYSTEM SHALL report the three models so the caller can persist
    # them: job-level provenance cannot describe three models in one column.
    _capture_models(monkeypatch)
    out = se.summarize_row(
        "/x.pdf",
        _row(),
        model="body-m",
        prompt="P",
        verify=True,
        title_model="title-m",
        audit_model="audit-m",
    )
    assert out["model"] == "body-m"
    assert out["titleModel"] == "title-m"
    assert out["auditModel"] == "audit-m"


def test_audit_provenance_is_absent_when_the_audit_did_not_run(monkeypatch):
    # WHEN the verify pass does not run, THE SYSTEM SHALL leave the audit model and fingerprint unset
    # rather than recording a value for a call that never happened.
    _capture_models(monkeypatch)
    out = se.summarize_row("/x.pdf", _row(), model="body-m", prompt="P", verify=False)
    assert out["auditModel"] is None
    assert out["auditFingerprint"] is None
    assert out["promptFingerprint"]  # the body prompt still has one


# --- prompt fingerprints -------------------------------------------------------------------------


def test_a_changed_category_prompt_changes_the_fingerprint():
    # WHEN any category prompt changes, THE SYSTEM SHALL produce a different fingerprint.
    assert summary_prompt_fingerprint("PRE", "prompt A") != summary_prompt_fingerprint(
        "PRE", "prompt B"
    )


def test_a_changed_preamble_changes_the_fingerprint():
    # WHEN the shared preamble changes for a category, THE SYSTEM SHALL produce a different one.
    assert summary_prompt_fingerprint("PRE 1", "same") != summary_prompt_fingerprint(
        "PRE 2", "same"
    )


def test_the_separator_stops_concatenation_collisions():
    # Without a separator byte, ("ab","c") and ("a","bc") hash identically and two different prompt
    # sets become indistinguishable. This is the reason fingerprint() writes a NUL between parts.
    assert fingerprint("ab", "c") != fingerprint("a", "bc")


def test_the_same_prompt_gives_the_same_fingerprint_regardless_of_row_context(monkeypatch):
    # WHEN two rows of the same category are summarized and only one receives the record's other
    # diagnostic studies, THE SYSTEM SHALL give both the SAME fingerprint - the studies block is row
    # DATA, and hashing it would make the cohort query this exists to enable meaningless.
    _capture_models(monkeypatch)
    plain = se.summarize_row("/x.pdf", _row(category="5"), model="m", prompt="P", verify=False)
    withctx = se.summarize_row(
        "/x.pdf",
        _row(category="5"),
        model="m",
        prompt="P",
        verify=False,
        standalone_studies=[{"title": "MRI LUMBAR SPINE", "date": "01/02/24"}],
    )
    assert plain["promptFingerprint"] == withctx["promptFingerprint"]


def test_different_categories_get_different_fingerprints(monkeypatch):
    # WHEN two rows of DIFFERENT categories are summarized in one job, THE SYSTEM SHALL give them
    # different fingerprints - which is why a job-level hash cannot stand in for the per-row one.
    _capture_models(monkeypatch)
    one = se.summarize_row("/x.pdf", _row(category="1"), model="m", prompt="P-cat1", verify=False)
    three = se.summarize_row("/x.pdf", _row(category="3"), model="m", prompt="P-cat3", verify=False)
    assert one["promptFingerprint"] != three["promptFingerprint"]


def test_the_audit_fingerprint_tracks_the_verify_prompt_alone(monkeypatch):
    # WHEN the audit prompt changes, THE SYSTEM SHALL change the audit fingerprint and leave the
    # body prompt's fingerprint untouched - so a before/after can tell "the generator improved" from
    # "the audit is covering for it".
    _capture_models(monkeypatch)
    first = se.summarize_row("/x.pdf", _row(), model="m", prompt="P", verify=True)
    monkeypatch.setattr(se, "VERIFY_PROMPT", "a different audit prompt")
    second = se.summarize_row("/x.pdf", _row(), model="m", prompt="P", verify=True)
    assert first["auditFingerprint"] != second["auditFingerprint"]
    assert first["promptFingerprint"] == second["promptFingerprint"]
