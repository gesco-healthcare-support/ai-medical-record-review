"""`extract_header` and its honesty about a partial OCR read (#211).

The four fields it produces - patient first/last name, DOB, law firm - are reviewer-facing on the
landing table and travel into the deliverable. It used to read through
`extract_text_from_selected_pages`, which catches a per-page Tesseract failure and continues, so a
dropped page was indistinguishable from a page with no words on it.

The two failures are not equally safe, which is the whole point:

* EVERY page fails -> empty text -> blanks -> the reviewer meets four empty fields and fills them
  in. Visible and recoverable.
* ONE page fails -> a header built from what survived, which LOOKS complete.

The provider is stubbed throughout; no model call and no Tesseract.
"""

import json
import logging
from types import SimpleNamespace

import pytest

from app.config import Settings, get_settings
from app.services import extraction


def _stub_provider(
    monkeypatch, payload='{"first_name": "A", "last_name": "B", "dob": "", "lawfirm": ""}'
):
    """Stub the seam, not google-genai. `extract_header` asks `provider_for_stage("extract")`."""

    class _Provider:
        def generate_structured(self, **_kwargs):
            return SimpleNamespace(text=payload)

    monkeypatch.setattr(extraction, "provider_for_stage", lambda *_a, **_k: _Provider())


def _stub_ocr(monkeypatch, text, errored, pages=(1, 2, 3)):
    monkeypatch.setattr(
        extraction,
        "extract_pages_with_report",
        lambda pdf_path, p: (text, {"pages": list(pages), "errored": list(errored), "blank": []}),
    )


def test_a_partial_read_still_produces_a_header_but_says_so(monkeypatch, caplog):
    """The unsafe case. The header is still extracted - throwing away good text would be worse - but
    the pages that could not be read are named, so a wrong name or DOB is attributable afterwards."""
    _stub_provider(monkeypatch)
    _stub_ocr(monkeypatch, "text of the pages that survived", errored=[2])

    with caplog.at_level(logging.WARNING):
        header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header["first_name"] == "A"  # still extracted
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "PARTIAL" in logged
    assert "[2]" in logged, "the errored page must be named, not merely counted"
    assert "1 of 3" in logged


def test_a_clean_read_says_nothing(monkeypatch, caplog):
    """A warning on every record would make the signal worthless."""
    _stub_provider(monkeypatch)
    _stub_ocr(monkeypatch, "all of the text", errored=[])

    with caplog.at_level(logging.WARNING):
        header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header["first_name"] == "A"
    assert not [r for r in caplog.records if "PARTIAL" in r.getMessage()]


def test_a_total_failure_returns_blanks_without_calling_the_model(monkeypatch):
    """Unchanged behaviour, pinned: no text means no model call, and four empty fields the reviewer
    can see and fill in."""
    called = []

    class _Provider:
        def generate_structured(self, **_kwargs):
            called.append("model")
            return SimpleNamespace(text="{}")

    def _provider(*_a, **_k):
        # Recorded separately from the call itself: asking for a provider and calling it are one
        # expression, so reaching the resolver at all already means the guard above let a blank read
        # through. Both entries must stay absent, not just the second.
        called.append("provider")
        return _Provider()

    monkeypatch.setattr(extraction, "provider_for_stage", _provider)
    _stub_ocr(monkeypatch, "   ", errored=[1, 2, 3])

    header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header == {"first_name": "", "last_name": "", "dob": "", "lawfirm": ""}
    assert called == [], "a blank read must not spend a model call"


def test_a_malformed_model_reply_returns_blanks(monkeypatch):
    """Unchanged behaviour, pinned alongside the above so the partial-read change cannot disturb it."""
    _stub_provider(monkeypatch, payload="not json at all")
    _stub_ocr(monkeypatch, "some text", errored=[])

    assert extraction.extract_header("x.pdf", [1]) == {
        "first_name": "",
        "last_name": "",
        "dob": "",
        "lawfirm": "",
    }


def test_the_log_line_carries_page_numbers_and_nothing_else(monkeypatch, caplog):
    """PHI guard: the OCR text is the one thing that must never reach a log line here, and this
    function holds a page of it at the moment it warns."""
    secret = "PATIENT JANE DOE DOB 01/02/1980 ACME LAW"
    _stub_provider(monkeypatch)
    _stub_ocr(monkeypatch, secret, errored=[7])

    with caplog.at_level(logging.WARNING):
        extraction.extract_header("x.pdf", [7, 8])

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "[7]" in logged
    for fragment in ("JANE", "DOE", "01/02/1980", "ACME"):
        assert fragment not in logged, fragment


# --- what the call actually SENDS -------------------------------------------------------------
#
# The tests above all assert on what comes BACK. They passed unchanged when this service moved off
# google-genai onto the provider seam, which is exactly why they cannot be the whole of it: the
# request shape is the part the move could have altered.


def _capture(
    monkeypatch, payload='{"first_name": "A", "last_name": "B", "dob": "", "lawfirm": ""}'
):
    """Stub the provider and hand back the kwargs it was called with.

    `seen["_resolved_for"]` records the stage the SERVICE asked the resolver for. Without it the
    stub swallows a wrong stage silently - and resolving the transport through the wrong stage is
    precisely the defect that reached main in #318.
    """
    seen = {}

    class _Provider:
        def generate_structured(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(text=payload)

    def _resolver(stage, *_a, **_k):
        seen["_resolved_for"] = stage
        return _Provider()

    monkeypatch.setattr(extraction, "provider_for_stage", _resolver)
    return seen


def test_the_request_carries_the_lowercase_schema_and_the_extract_stage(monkeypatch):
    """WHEN extract_header runs, THE SYSTEM SHALL send the header schema, _HEADER_SYSTEM and the
    `extract` stage.

    THE SCHEMA IS ASSERTED LOWERCASE ON PURPOSE. Written in google-genai's uppercase spelling it
    would still work on Gemini - `to_gemini_schema` passes an already-uppercase name through - and
    silently fail to constrain any other backend. So the one dialect that proves nothing on the
    Gemini path is the one this has to pin.
    """
    seen = _capture(monkeypatch)
    _stub_ocr(monkeypatch, "some text", errored=[])

    extraction.extract_header("x.pdf", [1])

    # The TRANSPORT and the model must resolve through the same stage. `stage=` below only selects a
    # thinking budget; `_resolved_for` is what says which backend actually answers.
    assert seen["_resolved_for"] == "extract"
    assert seen["stage"] == "extract"
    assert seen["system"] == extraction._HEADER_SYSTEM
    assert seen["temperature"] == 0.0
    assert seen["schema"]["type"] == "object"
    assert seen["schema"]["properties"]["dob"]["type"] == "string"
    assert sorted(seen["schema"]["required"]) == ["dob", "first_name", "last_name", "lawfirm"]
    # This call has never carried a cap, and the seam sends the field only when it is set.
    assert "max_output_tokens" not in seen


def test_the_model_is_resolved_for_the_backend_not_read_from_genai_model(monkeypatch):
    """WHEN `extract` resolves to vllm, THE SYSTEM SHALL send VLLM_MODEL, not genai_model.

    Only the vllm case can catch this. On Gemini `model_for_stage("extract")` returns genai_model
    by construction, so a service reading the raw setting is indistinguishable from one asking the
    resolver - until a stage moves.

    Flipping the backend on the cached settings is enough because `model_for_stage` resolves at CALL
    time through `backend_for(stage)`. The summarize triple does not behave that way - it is fixed
    during validation - so the same trick would prove nothing there.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "vllm")
    monkeypatch.setattr(settings, "vllm_model", "served-by-the-pod/model")
    seen = _capture(monkeypatch, payload="{}")
    _stub_ocr(monkeypatch, "some text", errored=[])

    extraction.extract_header("x.pdf", [1])

    assert seen["model"] == "served-by-the-pod/model"
    # Control: the two genuinely differ here, or the assertion above passes trivially.
    assert settings.genai_model != "served-by-the-pod/model"


# --- the covering letter's fields (HEADER_PREFILL_FIELDS) -------------------------------------
#
# Reviewers type attorney, doctor, letter date and pages received on every record. The same call can
# read them from the covering letter, but only the fields the setting turns on are asked for, and a
# value is kept only in the form the header stores it in.

_LETTER = ("attorney_name", "doctor", "letter_date", "pages_received")


def _letter_reply(**fields):
    return json.dumps({"first_name": "A", "last_name": "B", "dob": "", "lawfirm": "", **fields})


def test_with_no_letter_field_asked_the_call_is_the_four_field_one(monkeypatch):
    """GUARD: the default (empty setting) sends today's system, schema and instruction exactly."""
    monkeypatch.setattr(get_settings(), "header_prefill_fields", "")
    seen = _capture(monkeypatch, payload=_letter_reply(doctor="Pelton"))
    _stub_ocr(monkeypatch, "some text", errored=[])

    header = extraction.extract_header("x.pdf", [1])

    assert seen["system"] == extraction._HEADER_SYSTEM
    assert seen["schema"] is extraction._HEADER_SCHEMA
    assert seen["parts"][0].text.startswith(
        "Extract the patient's first name and last name (separately), the patient's date of birth "
        "(mm/dd/yyyy), and the attorney/law firm that sent the record from this text:\n\n"
    )
    assert sorted(header) == ["dob", "first_name", "last_name", "lawfirm"]


def test_the_setting_decides_which_letter_fields_are_asked(monkeypatch):
    """DEMONSTRATES: a field the setting names joins the schema, the required list and the reply."""
    monkeypatch.setattr(get_settings(), "header_prefill_fields", "pages_received, doctor")
    seen = _capture(monkeypatch, payload=_letter_reply(doctor="Pelton", pages_received="120"))
    _stub_ocr(monkeypatch, "some text", errored=[])

    header = extraction.extract_header("x.pdf", [1])

    assert seen["system"] == extraction._HEADER_SYSTEM + extraction._LETTER_SYSTEM
    assert sorted(seen["schema"]["properties"]) == [
        "dob",
        "doctor",
        "first_name",
        "last_name",
        "lawfirm",
        "pages_received",
    ]
    assert seen["schema"]["required"][-2:] == ["doctor", "pages_received"]
    assert "covering letter also the evaluating physician's last name" in seen["parts"][0].text
    assert header["doctor"] == "Pelton"
    assert header["pages_received"] == "120"
    assert "attorney_name" not in header


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"doctor": "Dr. Pelton"}, {"doctor": "Pelton"}),
        ({"doctor": "PELTON, M.D."}, {"doctor": "Pelton"}),
        ({"doctor": "Someone Elsewhere"}, {"doctor": ""}),
        ({"doctor": "Pelton and Ziv"}, {"doctor": ""}),
        ({"letter_date": "8/5/2026"}, {"letter_date": "08/05/2026"}),
        ({"letter_date": "August 5, 2026"}, {"letter_date": ""}),
        ({"letter_date": "13/05/2026"}, {"letter_date": ""}),
        ({"pages_received": "0120"}, {"pages_received": "120"}),
        ({"pages_received": "about 120"}, {"pages_received": ""}),
        ({"pages_received": "0"}, {"pages_received": ""}),
        ({"attorney_name": "  Jane   Synthetic, Esq. "}, {"attorney_name": "Jane Synthetic, Esq."}),
    ],
)
def test_a_letter_field_is_kept_only_in_the_form_the_header_stores(monkeypatch, reply, expected):
    """DEMONSTRATES: a doctor off the dropdown list, a date not mm/dd/yyyy or a count that is not
    a whole number above 0 comes back '' - nothing to pre-fill - rather than a wrong value."""
    _capture(monkeypatch, payload=_letter_reply(**reply))
    _stub_ocr(monkeypatch, "some text", errored=[])

    header = extraction.extract_header("x.pdf", [1], letter_fields=_LETTER)

    assert {field: header[field] for field in expected} == expected


def test_a_blank_or_malformed_read_blanks_the_asked_letter_fields_too(monkeypatch):
    """GUARD: the reply always carries every asked field, as '' when nothing could be read."""
    _stub_provider(monkeypatch, payload="not json at all")
    _stub_ocr(monkeypatch, "some text", errored=[])
    assert extraction.extract_header("x.pdf", [1], letter_fields=("doctor",))["doctor"] == ""

    _stub_ocr(monkeypatch, "   ", errored=[1])
    blank = extraction.extract_header("x.pdf", [1], letter_fields=_LETTER)
    assert {field: blank[field] for field in _LETTER} == dict.fromkeys(_LETTER, "")


def _record(page_count=50, **stored):
    fields = dict.fromkeys(_LETTER, None)
    fields.update(stored)
    return SimpleNamespace(page_count=page_count, **fields)


_FOUND = {
    "attorney_name": "Jane Synthetic",
    "doctor": "Pelton",
    "letter_date": "08/05/2026",
    "pages_received": "47",
}


def test_prefill_fills_an_empty_field_and_names_it():
    """DEMONSTRATES: every found field lands on a record that has none of them, the count as int."""
    record = _record()

    filled = extraction.prefill_letter_fields(record, _FOUND)

    assert filled == list(_LETTER)
    assert record.pages_received == 47
    assert record.doctor == "Pelton"


def test_prefill_never_overwrites_a_value_a_reviewer_typed():
    """DEMONSTRATES: a re-identify or a second detect leaves a typed value alone - '' counts as
    empty, whitespace too, and a typed page count of any size is a value."""
    record = _record(attorney_name="Typed Person", doctor="", letter_date="  ", pages_received=12)

    filled = extraction.prefill_letter_fields(record, _FOUND)

    assert filled == ["doctor", "letter_date"]
    assert record.attorney_name == "Typed Person"
    assert record.pages_received == 12


def test_prefill_keeps_a_page_count_only_up_to_the_pages_on_file():
    """DEMONSTRATES: a stated count above the record's own page count is not stored."""
    over = _record(page_count=40)
    assert extraction.prefill_letter_fields(over, {"pages_received": "41"}) == []
    assert over.pages_received is None

    equal = _record(page_count=40)
    assert extraction.prefill_letter_fields(equal, {"pages_received": "40"}) == ["pages_received"]


def test_prefill_with_nothing_found_changes_nothing():
    """GUARD: the default four-field reply carries no letter field, so nothing is written."""
    record = _record()
    assert extraction.prefill_letter_fields(record, {"first_name": "A", "lawfirm": "B"}) == []
    assert all(getattr(record, field) is None for field in _LETTER)


@pytest.mark.parametrize("value", ["doctor,fax_number", "Doctor"])
def test_a_prefill_field_nobody_can_act_on_refuses_startup(monkeypatch, value):
    """A typo refuses startup instead of leaving a field the operator turned on never filled."""
    monkeypatch.setenv("HEADER_PREFILL_FIELDS", value)
    with pytest.raises(RuntimeError, match="HEADER_PREFILL_FIELDS"):
        Settings()  # type: ignore[call-arg]


def test_the_prefill_fields_parse_in_one_order(monkeypatch):
    """GUARD: spaces and empty entries are ignored, and the order is the fields' own."""
    monkeypatch.setenv("HEADER_PREFILL_FIELDS", " pages_received ,attorney_name,, ")
    parsed = Settings().header_letter_fields()  # type: ignore[call-arg]
    assert parsed == ("attorney_name", "pages_received")

    monkeypatch.setenv("HEADER_PREFILL_FIELDS", "")
    assert Settings().header_letter_fields() == ()  # type: ignore[call-arg]
