"""llm_classify model wiring: it uses the (cheap) classify_model by default and honors an override.

An A/B showed Flash-Lite matches full Flash on the labeled taxonomy examples (identical accuracy,
100% agreement), so classification runs on the cheaper tier. These tests pin the model selection;
the Vertex call itself is stubbed.
"""

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import classification


def _stub_generate(captured):
    def fake(client, **kwargs):
        captured["model"] = kwargs.get("model")
        return SimpleNamespace(text="1")  # a valid category id

    return fake


def test_llm_classify_defaults_to_classify_model(monkeypatch):
    captured = {}
    monkeypatch.setattr(classification, "get_genai_client", lambda: object())
    monkeypatch.setattr(classification, "generate_with_retry", _stub_generate(captured))
    assert classification.llm_classify("Progress Report") == "1"
    assert captured["model"] == get_settings().classify_model


def test_llm_classify_honors_model_override(monkeypatch):
    captured = {}
    monkeypatch.setattr(classification, "get_genai_client", lambda: object())
    monkeypatch.setattr(classification, "generate_with_retry", _stub_generate(captured))
    classification.llm_classify("anything", model="gemini-2.5-flash")
    assert captured["model"] == "gemini-2.5-flash"


# Administrative paperwork that leads a record: it accompanies the evaluation rather than being one,
# so it belongs in General (100) - which is also unchecked for summarization. Titles are SYNTHETIC.
@pytest.mark.parametrize(
    "title",
    [
        "Acme Medical Records Routing Slip",
        "Records Routing Sheet",
        "Email - AME Evaluation Cover Letter",  # the QME/AME rule used to claim this one
        "Cover Letter - Submission of Medical Records",
        "Transmittal Letter",
        "Email",
        "Email Correspondence",
        "Declaration of Compliance",
        "Declaration of Custodian of Records",
        "Declaration Under Penalty of Perjury",
        "Proof of Service by Mail",
        "Certificate of Mailing",
        "Schedule of Records",
        "Records Request",
        "Agreed Medical Evaluation Request",
        "Notice of Independent Medical Examination",
    ],
)
def test_administrative_titles_are_general(title):
    assert classification.match_rules(title) == "100"


# The clinical documents these patterns sit next to must keep their own category: a false positive
# would silently drop a real report out of summarization.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("QME Panel Report", "13"),
        ("Agreed Medical Evaluator Report", "13"),
        ("Supplemental QME Report", "12"),
        ("Request for Authorization", "10"),
        ("Primary Treating Physician Progress Report (PR-2)", "1"),
        ("MRI Lumbar Spine", "3"),
        ("Deposition of the Applicant", "9"),
        ("Operative Report", "8"),
        ("Fax - Updated Progress Note", "1"),  # a fax cover line over a real progress note
    ],
)
def test_clinical_titles_keep_their_category(title, expected):
    assert classification.match_rules(title) == expected


# A title routinely names BOTH the wrapper and the document inside it. The substantive document is
# what the reviewer needs summarized, so it must win - otherwise the record silently loses a report.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Transmittal Letter - MRI Lumbar Spine", "3"),
        ("Cover Letter - PR-2 Progress Report", "1"),
        ("Correspondence - Operative Report", "8"),
        ("Cover Letter and Operative Report - Dr Sample", "8"),
        ("Request for Authorization for QME evaluation", "10"),
        ("Physical Therapy Evaluation Appointment", "5"),
        ("Chiropractic Evaluation Request", "5"),
        ("Email - Deposition Transcript", "9"),
        ("Supplemental QME Report - Cover Letter", "12"),
    ],
)
def test_document_type_beats_administrative_wrapper(title, expected):
    assert classification.match_rules(title) == expected


# The segmenter folds a cover sheet into the document it travels with and titles the record from that
# cover page (services/gemini.py), so "wrapper + document" titles are its normal output. When the
# title names a document, the wrapper must not decide - least of all for a QME/AME evaluation, the
# most valuable document in the file.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AME Report with Cover Letter", "13"),
        ("QME Report - Proof of Service", "13"),
        ("Cover Letter - AME Report of Dr Sample", "13"),
        ("Email Correspondence - QME Report", "13"),
        ("Panel QME Report with Declaration of Service", "13"),
    ],
)
def test_evaluation_reports_survive_their_cover_page(title, expected):
    assert classification.match_rules(title) == expected


# A report type with no keyword rule must fall through to the embedding + LLM stages rather than be
# answered - at high confidence, with no review flag - by the wrapper.
@pytest.mark.parametrize(
    "title",
    [
        "Cover Letter - Psychological Evaluation Report",
        "Correspondence - Work Status Report",
        "Transmittal Letter - Nerve Conduction Study Report",
        "Cover Letter - Narrative Medical Report",
    ],
)
def test_unruled_report_behind_a_wrapper_reaches_the_cascade(title):
    assert classification.match_rules(title) is None


# D-01/D-02 split 3 (studies performed ON THE BODY) from 14 (tests run on a SPECIMEN) and rewrote
# both taxonomy descriptions accordingly. That reached the embedding + LLM stages only: `laborator`
# stayed in the category-3 RULE, and rules short-circuit before either stage runs, so a laboratory
# title was answered 3 at high confidence with no review flag. These pin the split at the rules stage
# too, where the reviewer gets no signal that anything was decided.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("LABORATORY RESULTS - COMPREHENSIVE METABOLIC PANEL", "14"),
        ("Laboratory Results", "14"),
        ("Laboratory Test Results", "14"),
        ("Lab Results", "14"),
        ("Test Results", "14"),
    ],
)
def test_specimen_results_are_laboratory_not_imaging(title, expected):
    assert classification.match_rules(title) == expected


# The other half of D-01/D-02: dropping `laborator` from rule 3 must not cost imaging its priority.
# "Radiology Test Results" matches BOTH rules, and 3 still precedes 14, so the modality wins - which
# is why the fix is a deleted token rather than a reordering of the two rules.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Radiology Test Results", "3"),
        ("MRI Lumbar Spine", "3"),
        ("Unattended Sleep Study", "3"),
        ("Ultrasound Report", "3"),
        ("NCS/EMG Report", "3"),
        ("Diagnostic Study (X-Ray, MRI, CT scan)", "3"),
    ],
)
def test_modality_studies_keep_category_three(title, expected):
    assert classification.match_rules(title) == expected


# The third outcome of dropping `laborator`, and the one the two suites above do not reach: both only
# cover titles containing "results", which the category-14 rule matches. A laboratory title WITHOUT
# that word now matches no rule at all and falls through to the embedding + LLM cascade.
#
# That is the intended result rather than a gap. D-01/D-02 rewrote both taxonomy descriptions so the
# cascade answers 14 for specimen work, and a cascade answer carries a confidence value the reviewer
# can see - where the old rule hit returned "high" with needs_review=False and no signal at all. So
# the fix trades a silent wrong answer for a visible judged one. Pinned because "falls through" is
# easy to mistake for "regressed" if these ever start matching a rule again.
@pytest.mark.parametrize("title", ["Laboratory Report", "Blood Work Panel", "CBC", "Urinalysis"])
def test_specimen_titles_without_results_reach_the_cascade(title):
    assert classification.match_rules(title) is None


# An evaluator's NAME in an administrative title used to answer 13, because _ADMIN_RULES listed only
# "cover"/"transmittal" letters and anchored declarations to `^declaration`. Both gaps let the bare
# `ame` in rule 13 answer paperwork, at high confidence with no review flag.
#
# Ground truth: the human deliverable for record 7fb2b543 summarized 2 documents from 61 pages and
# listed its own excluded pages as "email, cover letter, declaration, joint AME letter, AME or QME
# declaration of service". The app summarized 4 - the two extras are exactly the last two of those.
@pytest.mark.parametrize(
    "title",
    [
        "Joint AME Letter",
        "AME Letter",
        "QME Letter",
        "PQME Letter",
        "QME Declaration of Service",
        "AME Declaration of Service",
    ],
)
def test_evaluator_named_paperwork_is_general_not_an_evaluation(title):
    assert classification.match_rules(title) == "100"


# The safety half: withholding 13 must not withhold a REAL document type that happens to share the
# title. _EVALUATOR_MENTION excludes only 13, so rule 12 still answers a supplemental - which is what
# makes the two additions above safe rather than a blanket "anything saying AME is paperwork".
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Supplemental AME Letter", "12"),
        ("Supplemental QME Report - Cover Letter", "12"),
        ("Transmittal Letter - MRI Lumbar Spine", "3"),
        ("Cover Letter - PR-2 Progress Report", "1"),
        ("QME Panel Report", "13"),
    ],
)
def test_a_real_document_type_still_beats_the_administrative_match(title, expected):
    assert classification.match_rules(title) == expected


# KNOWN BROKEN, pinned deliberately rather than left to be rediscovered. The same record's other
# excluded page is "AME or QME Declaration of Service of Medical - Legal Report", and the additions
# above do NOT fix it: _DOCUMENT_NOUN matches "Report", which stands the administrative rules down by
# design, so rule 13 answers it again.
#
# That is not a pattern gap - it is _DOCUMENT_NOUN being unable to tell "this IS a report" from "this
# is paperwork ABOUT a report". Widening the noun test or reordering the checks affects every wrapper
# title in the suite above, so it needs a decision rather than a patch. xfail(strict=False) so the day
# someone fixes it this reports XPASS instead of silently passing.
@pytest.mark.xfail(
    strict=False,
    reason="_DOCUMENT_NOUN matches 'Report' and stands the admin rules down; needs a design call",
)
def test_declaration_of_service_of_a_report_is_still_misfiled():
    assert (
        classification.match_rules("AME or QME Declaration of Service of Medical - Legal Report")
        == "100"
    )


def test_general_corpus_names_the_administrative_documents():
    """The embedding + LLM stages read this text, so it must describe what actually lands here."""
    from app.services.taxonomy import CATEGORIES

    described = f"{CATEGORIES['100'].description} {' '.join(CATEGORIES['100'].examples)}".lower()
    for word in ("routing", "correspondence", "declaration", "records request"):
        assert word in described
