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


# Was xfail in #119: the same record's other excluded page, "AME or QME Declaration of Service of
# Medical - Legal Report", still answered 13 because _DOCUMENT_NOUN matched "Report" and stood every
# administrative rule down. Fixed by splitting those rules into wrapper-capable and standalone - a
# declaration of service IS the filing, so the noun names what it is ABOUT, not what the pages are.
@pytest.mark.parametrize(
    "title",
    [
        "AME or QME Declaration of Service of Medical - Legal Report",
        "Declaration of Service of Medical Report",
        "Proof of Service of QME Report",
    ],
)
def test_service_paperwork_naming_its_object_is_not_rescued_by_the_noun(title):
    assert classification.match_rules(title) == "100"


# The boundary, pinned rather than left to be rediscovered. Suppressing the noun gets the title to
# the final line, but that line still lets a document-type rule answer, so a service receipt naming
# a type the RULES cover keeps that category instead of General.
#
# Not fixed here because the blunt version - General whenever service paperwork fires - was written
# and measured first, and it regressed real documents: "Request for Authorization for Evaluation"
# matches both the evaluation-notice rule and rule 10, and dropped from 10 to 100. Needs eData's
# answer on whether paperwork ever outranks a named document type. Constructed, not yet observed.
@pytest.mark.xfail(
    strict=False,
    reason="service paperwork still yields to a document-type rule; needs eData's answer",
)
def test_service_receipt_naming_a_covered_document_type_is_still_misfiled():
    assert classification.match_rules("Proof of Service of Deposition Transcript") == "100"


# The other half of that split, pinned so a future widening cannot quietly take it: a cover letter,
# transmittal letter or email genuinely travels ON TOP of a record, so a document noun must still
# stand THOSE down and let the real document answer.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Cover Letter - PR-2 Progress Report", "1"),
        ("Transmittal Letter - Operative Report", "8"),
        ("Supplemental QME Report - Cover Letter", "12"),
        ("Cover Letter - Psychological Evaluation Report", None),
    ],
)
def test_wrapper_paperwork_still_yields_to_the_document_it_carries(title, expected):
    assert classification.match_rules(title) == expected


# A named document type outranks a bare evaluator mention. Rule 13 is second in _RULES and
# first-match-wins, so "AME Deposition Transcript" answered 13 - the AME being QUESTIONED, filed as
# the AME's own report and summarized with the evaluation prompt, which asks for diagnoses,
# causation and apportionment a transcript does not carry.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AME Deposition Transcript", "9"),
        ("AME Deposition", "9"),
        ("QME MRI Report", "3"),
        ("AME Operative Report", "8"),
        ("QME Laboratory Results", "14"),
    ],
)
def test_a_named_document_type_outranks_a_bare_evaluator_mention(title, expected):
    assert classification.match_rules(title) == expected


# The limit of that, and the reason _EVALUATOR_YIELDS_TO is an explicit set rather than a reorder.
# P&S, MMI and progress language describes what an EVALUATION concludes, so those titles are the
# evaluator's own report and must stay 13. Moving rule 13 down the list instead would have taken
# these with it, because rules 1 and 2 sit ahead of imaging/operative/deposition/lab.
@pytest.mark.parametrize(
    "title",
    [
        "AME Report",
        "AME Evaluation",
        "QME Panel Report",
        "QME Re-Evaluation Report",
        "AME Permanent and Stationary Report",
        "AME Progress Report",
        "Agreed Medical Evaluator Report",
    ],
)
def test_the_evaluators_own_report_is_still_an_evaluation(title):
    assert classification.match_rules(title) == "13"


# Recurring workers-comp paperwork that NO rule answers, so every one of these reaches the embedding
# + LLM cascade. This is the INVERSE of the #107 and #119 defects: there a rule fired wrongly, here
# there is no rule at all - for document types that recur constantly in these files.
#
# Ground truth, from a 229-page record run end to end against its human deliverable on 2026-08-18:
#
#   - the human's own list of pages not remarked upon names "physician's return-to-work and voucher
#     report" and "emergency patient record" VERBATIM. We summarized both, the first into category 2
#     (PR-4 / Permanent & Stationary), which is not a plausible reading of a return-to-work voucher
#   - "WORK STATUS REPORT" appeared TEN times in that one record. Nine were categorized 1 and
#     summarized; one was categorized 100 and excluded. Same type, same document, two answers -
#     because what decides them is the cascade, and the cascade is not deterministic
#
# NOT fixed here, deliberately. Which category each of these belongs in is a taxonomy decision, and
# guessing the target is how the evaluator rules came to need #107 and #119. So this asserts only the
# incontestable part - that a type recurring ten times in one record ought to be answered by a rule
# rather than re-decided per occurrence - and asserts nothing about WHICH category. xfail(strict=False),
# so the day rules land these report XPASS instead of the finding living only in an email.
@pytest.mark.xfail(
    strict=False,
    reason="no rule answers these recurring administrative types; the cascade decides them, and not "
    "deterministically - the target categories are a taxonomy call",
)
@pytest.mark.parametrize(
    "title",
    [
        "WORK STATUS REPORT",
        "Physician's Return-to-Work & Voucher Report",
        "Emergency Provider Report",
        # Added from two further records (267 and 300 pages) run the same way on 2026-08-18. These
        # three are the ones that cost DELIVERED content rather than just a wrong category, so they
        # matter more than the seven above:
        #   - "Extracorporeal Shockwave Treatment Report" x4, 28 pages -> 100, unchecked, dropped.
        #     The human wrote FOUR separate entries for them, on our four dates exactly.
        #   - "Functional Improvement Measurements", 14 pages -> 100, dropped. The human wrote an
        #     entry for it on our date exactly.
        #   - "Utilization Review Letter" x4, 8 pages -> 100, dropped. The human summarized it.
        # In the same record, "Acupuncture Report" HAS a rule (5) and was summarized correctly - so
        # the presence of a rule, not the clinical content, is what decided whether it survived.
        #
        # 2026-08-20 (#137): the first two moved out of this list into
        # test_edata_confirmed_types_are_physical_therapy - both category 5.
        # 2026-08-21 (#138): the third moved out too. "Utilization Review Letter" is answered by
        # the new category 15 and pinned in test_utilization_review_titles_are_category_fifteen.
        # Each xpassed here the moment its rule landed, which is what this pin is for, so all
        # three are now gone and only the types still waiting on a decision remain above.
    ],
)
def test_recurring_paperwork_is_answered_by_a_rule(title):
    assert classification.match_rules(title) is not None


# Hospital and registration paperwork the human deliverables name VERBATIM in their excluded-pages
# lists, so the expected answer here is the reviewer's own, not our reading of it. Four of these were
# xfail pins from #121/#123 ("this type ought to be answered by a rule"); they are assertions now.
#
# The point of the rule is DETERMINISM as much as correctness. Every one of these already reached 100
# through the cascade most of the time - but "most of the time" is the defect: measured on one 267-page
# record, `lab order` came out 100 on one occurrence and 3 on another, and `WORK STATUS REPORT` came out
# 1 nine times and 100 once. A rule makes the answer the same on every occurrence.
@pytest.mark.parametrize(
    "title",
    [
        # named in the excluded-pages list of the 267-page record
        "FACESHEET",
        "FACESHEET - OP Visit",
        "Data Conversion Encounter - FACESHEET",
        "Flowsheets - ALL",
        "After Visit Summary",
        "Coding Summary - HIM",
        "ER Registration",
        "Patient Information Sheet",
        "Hospital Consent for Treatment - Conditions of Admission",
        "Medication Administration",
        "ED Care Timeline",
        # named in the excluded-pages list of the 229-page record
        "Patient Referral",
        "Patient Signature Page",
        "Emergency Patient Record",
        # same family, and the type a facesheet arrives attached to
        "Admission Record",
        "Inpatient Record",
    ],
)
def test_hospital_and_registration_paperwork_is_general(title):
    assert classification.match_rules(title) == "100"


# The other half of the same split, and the reason the list above stops where it does. These three are
# named in the SAME human exclusion lists, so the expected answer is equally clear - but a rule cannot
# deliver it, because `_DOCUMENT_NOUN` matches "report"/"notes"/"note" and stands the administrative
# rules down. That is the architectural question already pinned from #119, not a pattern gap, so they
# stay xfail rather than getting a rule that cannot fire.
@pytest.mark.xfail(
    strict=False,
    reason="_DOCUMENT_NOUN stands the admin rules down for report/note titles; needs the same design "
    "call as the #119 pin",
)
@pytest.mark.parametrize(
    "title",
    [
        "Physician's Return-to-Work & Voucher Report",
        "Interdisciplinary Notes",
        "Transmittal Note",
    ],
)
def test_paperwork_whose_title_carries_a_document_noun_is_general(title):
    assert classification.match_rules(title) == "100"


# Guards on the new patterns. Each is a title that CONTAINS one of the new phrases as a substring or
# near-miss and must not be dragged into General by it.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # "provider registration" contains "er registration" but not at a word boundary
        ("Provider Registration Form", None),
        # a real clinical document that merely mentions an admission
        ("Discharge Summary - Hospital Admission 03/04/2026", None),
        # the phrases are multi-word on purpose: a bare "summary" or "record" must not fire
        ("Operative Summary", None),
        ("Medical Record Review", None),
        # and a real document type still beats the administrative match where one fires
        ("Transmittal Letter - MRI Lumbar Spine", "3"),
        ("Cover Letter - PR-2 Progress Report", "1"),
    ],
)
def test_new_paperwork_patterns_do_not_overreach(title, expected):
    assert classification.match_rules(title) == expected


def test_general_corpus_names_the_administrative_documents():
    """The embedding + LLM stages read this text, so it must describe what actually lands here."""
    from app.services.taxonomy import CATEGORIES

    described = f"{CATEGORIES['100'].description} {' '.join(CATEGORIES['100'].examples)}".lower()
    for word in ("routing", "correspondence", "declaration", "records request"):
        assert word in described


# The first document-type question answered by the eData reviewers who write these reports by hand,
# rather than decided in-house: an Extracorporeal Shockwave Treatment Report (an M.D. at a pain
# management practice) and Functional Improvement Measurements (an L.Ac.) are both category 5.
#
# Both were answered 100 by the cascade before this, and 100 is unchecked for summarization, so all
# five occurrences reached no deliverable - four 7-page shockwave reports and one 14-page measurement
# sheet, 42 pages. The shockwave half is independently confirmed: on the reviewed copy of that record
# the reviewer moved all four rows from 100 to 5 by hand.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Extracorporeal Shockwave Treatment Report", "5"),
        ("EXTRACORPOREAL SHOCKWAVE TREATMENT REPORT", "5"),
        ("Extracorporeal Shock Wave Therapy", "5"),
        ("Shock-Wave Treatment Note", "5"),
        ("Functional Improvement Measurements", "5"),
        ("FUNCTIONAL IMPROVEMENT MEASUREMENTS", "5"),
    ],
)
def test_edata_confirmed_types_are_physical_therapy(title, expected):
    assert classification.match_rules(title) == expected


# The precision of that rule, and the reason `therapy|treatment` is required after the wave rather
# than matching the wave alone. Extracorporeal shock wave LITHOTRIPSY is a urology procedure for
# kidney stones and shares the first three words with the physical-therapy modality. A bare
# `extracorporeal shock ?wave` was written and measured first; it claimed these, which is what
# narrowed it.
@pytest.mark.parametrize(
    "title",
    [
        "Extracorporeal Shock Wave Lithotripsy Operative Report",
        "ESWL - Lithotripsy Procedure Note",
        "Extracorporeal Shock Wave Lithotripsy",
    ],
)
def test_lithotripsy_is_not_claimed_by_the_shockwave_rule(title):
    assert classification.match_rules(title) != "5"


# A shockwave title with NO therapy or treatment word is deliberately left to the cascade. One real
# row looks like this (7 pages, sitting at category 1) and no human has ruled on it, so the rule must
# not move it on our guess. Pinned because widening the pattern to catch it would look like an
# obvious improvement.
def test_a_bare_shockwave_mention_is_left_to_the_cascade():
    assert classification.match_rules("Shockwave Procedure") is None


# An evaluator's own report keeps priority: rules 12 and 13 both precede category 5, so a supplemental
# QME that happens to discuss shockwave therapy is still an evaluation.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Supplemental QME Report - Shockwave Therapy Review", "12"),
        ("AME Report - Functional Improvement Measurements", "13"),
    ],
)
def test_evaluator_reports_outrank_the_new_category_five_terms(title, expected):
    assert classification.match_rules(title) == expected


# Category 15, added 2026-08-21 and answered by Adam: a utilization review or independent medical
# review determination is its own document type. Before the rule, that one type was answered FOUR
# different ways across the corpus - 10 twelve times, 100 four times, 3 three times and 5 twice -
# and on the single reviewed copy a human put four identical documents into three categories.
@pytest.mark.parametrize(
    "title",
    [
        "Utilization Review Letter",
        "Utilization Review Determination",
        "UTILIZATION REVIEW - NON-CERTIFICATION",
        "Utilization Review - Modification",
        "Independent Medical Review Determination",
        "IMR Final Determination Letter",
    ],
)
def test_utilization_review_titles_are_category_fifteen(title):
    assert classification.match_rules(title) == "15"


# Placement, which is the whole design of that rule. It sits BELOW the evaluator rules and ABOVE
# every clinical modality rule, so a determination ABOUT an imaging or therapy request is a
# determination rather than an imaging or therapy report - while an evaluator's own report that
# happens to discuss utilization review stays an evaluation.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Evaluator reports keep priority.
        ("QME Report - Utilization Review History", "13"),
        ("Supplemental AME Report re Utilization Review", "12"),
        # A determination about a clinical modality is still a determination.
        ("Utilization Review - MRI Lumbar Spine", "15"),
        ("Utilization Review Determination - Physical Therapy", "15"),
        ("Utilization Review - Acupuncture Request", "15"),
        ("Utilization Review Response - Progress Report", "15"),
    ],
)
def test_utilization_review_rule_placement(title, expected):
    assert classification.match_rules(title) == expected


# The request and the answer stay apart. Category 10 is the treating physician ASKING; 15 is the
# reviewer answering. No observed title carries both phrases - measured over every row on the box -
# so this pins the intent rather than an observed case.
def test_a_bare_request_for_authorization_is_still_category_ten():
    assert classification.match_rules("Request For Authorization") == "10"
    assert classification.match_rules("RFA (Request For Authorization)") == "10"


# `imr` is matched as a whole word only. A three-letter token is the riskiest kind of rule, so the
# boundary is pinned: no longer word containing those letters may claim the category.
@pytest.mark.parametrize("title", ["Imring Report", "Simr Note", "IMRI Study"])
def test_imr_needs_a_word_boundary(title):
    assert classification.match_rules(title) != "15"


def test_the_utilization_review_migration_carries_the_same_text_as_the_constants():
    """The classifier reads the DB catalog first, so adding a category to taxonomy.py reaches a
    seeded box only through the migration. Same guard as
    test_the_catalog_migration_carries_the_same_text_as_the_constants, for the new row."""
    import importlib.util
    from pathlib import Path

    from app.services.seed_catalog import constants_categories

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "b3f7c02e91a4_utilization_review_category.py"
    )
    spec = importlib.util.spec_from_file_location("ur_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    by_id = {c["id"]: c for c in constants_categories()}
    assert migration.CATEGORY_ID in by_id, "category 15 missing from the constants"
    row = by_id[migration.CATEGORY_ID]
    assert row["name"] == migration._NAME
    assert row["description"] == migration._DESCRIPTION
    assert list(row["examples"]) == migration._EXAMPLES
    # The new category must be summarized by default, or the rule would move these documents out of
    # General and they would still reach no deliverable.
    assert row["summarize_default"] is True
    assert row["auto_assign"] is True


def test_category_fifteen_has_its_own_summary_prompt():
    """A category with no code prompt falls back to the general (100) one, which would summarize a
    determination as generic paperwork. `prompts.py` is the source of truth - no DB row is seeded for
    it, by design (see f1a83b5c60d2)."""
    from app.services.seed_catalog import code_summary_prompt

    prompt = code_summary_prompt("15")
    assert prompt is not None
    lowered = prompt.lower()
    # The three outcomes Adam named. A prompt offering only approved/denied would misreport a
    # partial authorization as one or the other, which misstates the treatment the patient got.
    for word in ("certif", "modif", "deni"):
        assert word in lowered, f"the prompt never mentions {word}"
    # Two requirements that carry the "short summary" instruction. The determination must be
    # reported even on an approval - this category gets no _C_VERDICT block, see summarize_engine's
    # _KNOWN - and the quoted treating history must NOT be summarized, or every UR letter becomes a
    # second copy of records already summarized elsewhere in the deliverable.
    assert "even when the request was approved" in lowered
    assert "do not summarize the medical records" in lowered
