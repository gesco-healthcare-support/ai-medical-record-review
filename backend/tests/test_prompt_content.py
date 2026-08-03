"""Prompt-text guarantees for the 2026-07-31 tester-feedback fixes (plan tasks 2 and 5).

Asserted on the rendered prompt rather than on `prompts.py`'s source, because the catalog is DB-first
(`catalog.get_prompt()`) with this dict as the code fallback: a test that read the file would pass
while the served prompt said something else. These are the properties a reviewer noticed were missing,
so they are worth pinning even though prompt text is not logic.
"""

from app.services.prompts import prompts


class TestCategory05ProcedureSessions:
    """Four Extracorporeal Shockwave rows in one record, same category and same title, came out 2
    labelled / 2 prose. The type matched none of category 5's eight document types, so it fell to the
    Daily Encounter catch-all - which sanctioned BOTH formats, its parenthetical inviting prose and its
    third example being bare prose. The model was choosing between two permitted formats, not
    misbehaving."""

    def test_a_procedure_session_type_exists_and_names_its_three_points(self):
        """WHEN the category 5 prompt is rendered, THE SYSTEM SHALL carry one procedure-session block
        whose points are Diagnosis, Body part being treated, and Treatment provided."""
        prompt = prompts["category_05"]
        assert prompt.count("Extracorporeal Shockwave Treatment,") == 1
        block = prompt[prompt.index("### Procedure or Injection Session") :]
        block = block[: block.index("</medical_document_type>")]
        assert "Diagnosis (if present)" in block
        assert "Body part being treated" in block
        assert "Treatment provided" in block

    def test_prose_is_the_fallback_only_when_neither_is_identifiable(self):
        """WHEN a category 5 document names a body part or a treatment, THE SYSTEM SHALL require the
        labelled points; prose remains available only when it names neither."""
        prompt = prompts["category_05"]
        assert "names a body part OR a treatment" in prompt
        assert "Only where it names NEITHER" in prompt
        # The old unconditional invitation is gone - it is what sanctioned prose on a labelled form.
        assert "If the points below are not in the document, just summarize" not in prompt

    def test_the_prose_length_exemplar_is_kept(self):
        """The third example stays: it is a legitimate length exemplar from PR #55 tracking the measured
        human median of 165 characters for this category. The fix is to say WHEN it applies, not to
        delete it."""
        assert "individual outpatient physical therapy" in prompts["category_05"]


class TestCategory01InterimHistory:
    """`Interim History` appeared in no prompt in the catalog, while category 1 says "DO NOT create
    points on your own" - so a document carrying that section had no sanctioned handling, and the same
    clinic's notes went both ways on the same patient."""

    def test_category_01_sources_the_hpi_point_from_an_interim_history(self):
        """WHEN a category 1 document contains an Interim History section, THE SYSTEM SHALL populate the
        History of Present Illness point from it rather than from the full history."""
        prompt = prompts["category_01"]
        assert "Interim History" in prompt
        assert "Interval History" in prompt  # the variant spelling the corpus also uses
        # It must work WITH the existing current-visit rule, not replace it.
        assert "covers the interval since the last visit" in prompt

    def test_the_medico_legal_categories_are_left_alone(self):
        """Categories 12 and 13 are REQUIRED to carry the full injury history, so naming Interim History
        there would tell a medico-legal evaluation to drop the history it exists to record."""
        assert "Interim History" not in prompts["category_12"]
        assert "Interim History" not in prompts["category_13"]
