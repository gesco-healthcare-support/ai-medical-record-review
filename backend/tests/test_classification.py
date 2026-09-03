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
    monkeypatch.setattr(classification, "get_genai_client", object)
    monkeypatch.setattr(classification, "generate_with_retry", _stub_generate(captured))
    assert classification.llm_classify("Progress Report") == "1"
    assert captured["model"] == get_settings().classify_model


def test_llm_classify_honors_model_override(monkeypatch):
    captured = {}
    monkeypatch.setattr(classification, "get_genai_client", object)
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
#
#     CAVEAT ADDED 2026-08-21, and it qualifies the method rather than this record. Asked directly,
#     the reviewers said they DO summarize the return-to-work voucher, mostly as a treating report -
#     so being named in one record's excluded-pages list does NOT establish that a type is always
#     excluded. It establishes that it was excluded THERE. The voucher now has a rule pointing at 1,
#     not 100. Every rule grounded in an exclusion list is sound on the same evidence this one was,
#     which is the point: that evidence is weaker than it reads, and a type that recurs is worth
#     asking about rather than inferring from a single list.
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
        # "WORK STATUS REPORT" left this list 2026-08-21: the reviewers answered it directly
        # (category 1) and it is pinned in test_work_status_is_a_treating_report.
        # "Physician's Return-to-Work & Voucher Report" left this list 2026-08-21: a rule
        # answers it now (category 1), pinned in
        # test_the_return_to_work_voucher_is_a_treating_report.
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


# The other half of the same split. When this was written all three were blocked by `_DOCUMENT_NOUN`
# standing the administrative rules down for a report/note title, and the expected answer was clear
# with no way to express it.
#
# 2026-08-21: the voucher type is decided and has moved to its own test. These two remain xfail, but
# for a DIFFERENT and much weaker reason - measured on the box, every observed row of both already
# answers 100 through the cascade (three distinct titles for the first, one for the second), so
# neither is costing content and neither has earned a rule. The pin now records "we could, and chose
# not to" rather than "we cannot". Left xfail rather than deleted so it still fires if a rule appears.
@pytest.mark.xfail(
    strict=False,
    reason="answered 100 by the cascade on every observed row, so no rule was added; a rule would "
    "buy determinism these are not visibly missing and would skip the review flag",
)
@pytest.mark.parametrize(
    "title",
    [
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


# The return-to-work voucher, asked and answered 2026-08-21. It had no rule and the cascade guessed
# each time: the same title is answered 1, 2 AND 13 across 17 rows and 25 pages, 10 of them
# delivered. Two of those rows sit in 13, which summarizes a one-page form with the medical-legal
# evaluation prompt and its eighteen points.
#
# THE DESTINATION IS 1, AND THAT REVERSED OUR READING. The 229-page record's excluded-pages list
# names the type verbatim, and neither that report nor the 420-page one has an entry on its date, so
# the first reading was that reviewers exclude it and it belonged in 100. Asked directly, the answer
# was the opposite: they want it summarized, mostly as a treating report. Where it arrives as its own
# document they summarize it separately; where it arrives behind a report they merge the two, which
# is a boundary decision no category rule can express and which the reviewer already makes by hand.
# 1 is therefore PROVISIONAL - feedback pending on whether it stays there.
@pytest.mark.parametrize(
    "title",
    [
        "Physician's Return-to-Work & Voucher Report",
        "PHYSICIAN'S RETURN-TO-WORK & VOUCHER REPORT",
        "Physician's Return to Work and Voucher Report",
        "Return-to-Work & Voucher Report - Supplemental Job Displacement",
        # Either order, because the pattern carries both arms.
        "Voucher and Return to Work Report",
    ],
)
def test_the_return_to_work_voucher_is_a_treating_report(title):
    assert classification.match_rules(title) == "1"


# BOTH tokens are required, and this is the guard that made the measurement worth doing. Two real
# titles say return-to-work and are clinical documents in their own right - one category 1, one
# category 5 (physical therapy), one page each, both delivered. Claiming them on the bare phrase
# would drag the therapy note out of the category whose prompt is written for it.
@pytest.mark.parametrize(
    "title",
    [
        "Return to Work Authorization",
        "Return-to-Work Note",
        "Physician's Return-to-Work Report",
    ],
)
def test_return_to_work_without_a_voucher_is_left_to_the_cascade(title):
    assert classification.match_rules(title) is None


# An evaluation that DISCUSSES the voucher is still the evaluation. Rules 12 and 13 sit ahead of this
# one, so the ordering carries it; pinned because moving the voucher rule up the list would silently
# reclassify an evaluation as a treating report.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AME Report - Return-to-Work & Voucher Discussion", "13"),
        ("QME Report re Return-to-Work and Voucher Eligibility", "13"),
        ("Supplemental QME Report - Return-to-Work & Voucher", "12"),
    ],
)
def test_an_evaluation_discussing_the_voucher_stays_an_evaluation(title, expected):
    assert classification.match_rules(title) == expected


# A known and deliberate limitation, pinned so it is not mistaken for a bug. The pattern requires
# BOTH tokens, so a voucher document that never says return-to-work reaches the cascade instead. That
# is the safe direction: `voucher` alone would match an evaluation that merely discusses one, and no
# such title has been observed to measure the cost of either choice.
def test_a_voucher_without_return_to_work_reaches_the_cascade():
    assert classification.match_rules("Supplemental Job Displacement Voucher") is None


# Work status reports, answered by the reviewers 2026-08-21: "In the case of Work Status, we will
# count that as Category 1." Before this they had NO rule and the cascade gave four different answers
# across 89 rows - 66 as category 1, 14 as 100 (dropped, 17 pages), 8 as category 2 and 1 as
# category 5. The same form, four answers.
@pytest.mark.parametrize(
    "title",
    [
        "WORK STATUS REPORT",
        "Work Status Report",
        "Work Status",
        "Primary Treating Physician's Work Status Report",
        "Work Status Update",
    ],
)
def test_work_status_is_a_treating_report(title):
    assert classification.match_rules(title) == "1"


# The rules that precede category 1 still win, so a title carrying both keeps the more specific
# document. Pinned because "work status" is a common phrase and could easily be read as claiming
# every title it appears in.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("QME Report - Work Status", "13"),
        ("AME Work Status Report", "13"),
        ("Supplemental QME Report - Work Status", "12"),
        ("Permanent and Stationary Report with Work Status", "2"),
    ],
)
def test_a_more_specific_document_outranks_work_status(title, expected):
    assert classification.match_rules(title) == expected


# "work capacity" is a plausible sibling phrase that appears on ZERO titles on the box, so it is
# deliberately NOT in the pattern. Pinned so adding it later is a decision rather than a drift.
def test_work_capacity_is_not_claimed_without_evidence():
    assert classification.match_rules("Work Capacity Evaluation") is None


# Concentra titles its form "Work Activity Status Report", and the word between defeats the literal
# `work status` token added above - so the whole family had NO rule and the cascade re-decided every
# occurrence: 3 distinct titles, 70 rows, 71 pages, answered 67 x category 1, 2 x category 2 and
# 1 x category 100. Reported by Adam 2026-08-28 after testing a 207-page record, where the stray 100
# was left unchecked for summarization and its page reached no deliverable.
#
# `method` (#189) names the mechanism on that record: the dropped row is `llm-disagree` while the
# other TWELVE occurrences in the SAME document on the SAME build are `llm+embedding` -> 1.
@pytest.mark.parametrize(
    "title",
    [
        "Work Activity Status Report",
        "WORK ACTIVITY STATUS REPORT",
        "Concentra Work Activity Status Report",
        # What the source document calls itself in its own Document Type field, and therefore a
        # title segmentation can legitimately produce.
        "Activity Status and Restrictions Report",
        "Activity Status Report",
    ],
)
def test_work_activity_status_is_a_treating_report(title):
    assert classification.match_rules(title) == "1"


# The same precedence the plain `work status` token has: a title carrying a more specific document
# keeps it. Pinned separately because `activity status` is the broader of the two phrases and so is
# the one more likely to be read as claiming every title it appears in.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("QME Report - Work Activity Status", "13"),
        ("Permanent and Stationary Report with Work Activity Status", "2"),
        # The document the reporter wants this merged INTO still wins on its own title, so ruling
        # the attachment cannot re-file the report it travels with.
        ("Doctor's First Report of Occupational Injury or Illness", "2"),
        ("California - Doctor's First Report of Injury/Illness (5021)", "2"),
        ("Physical Therapy Activity Status", "5"),
    ],
)
def test_a_more_specific_document_outranks_activity_status(title, expected):
    assert classification.match_rules(title) == expected


# "work ability" and "work capacity" are plausible siblings that appear on ZERO titles on the box,
# so they stay out. Same reasoning as test_work_capacity_is_not_claimed_without_evidence, restated
# here so widening the phrase later is a decision rather than drift from this change.
@pytest.mark.parametrize(
    "title",
    ["Work Ability Form", "Work Capacity Evaluation", "Activity Log", "Status Report"],
)
def test_no_rule_is_claimed_for_the_unobserved_siblings(title):
    assert classification.match_rules(title) is None


# The two lists are a PAIR. `_TREATING_VISIT_WITHOUT_FOLLOWUP` is #181's mirror of rule 1: it decides
# whether a follow-up token surrenders category 1 to a modality. A token added to rule 1 and not to
# the mirror makes that synonym behave differently from its twin, and the divergence only shows on a
# title carrying BOTH a follow-up token AND a modality - which is why the tests for either change
# alone missed it. Caught by @adrian-g on review of this change; it had already happened once, when
# #146 added `work status`.
#
# Asserted as PAIRS rather than as expected values, deliberately. Pinning "-> 1" would pass if a
# later change moved both synonyms somewhere else together, which is fine, and would fail for a
# reason unrelated to the invariant. What must never happen is the two answering differently.
@pytest.mark.parametrize(
    "modality",
    ["MRI of the Lumbar Spine", "Operative Report", "Deposition", "Laboratory Results", "X-Ray"],
)
def test_work_status_and_activity_status_answer_alike_with_a_followup_token(modality):
    work = classification.match_rules(f"Work Status Report Follow-Up {modality}")
    activity = classification.match_rules(f"Activity Status Report Follow-Up {modality}")
    assert work == activity, f"the two synonyms diverge on {modality!r}: {work} vs {activity}"


def test_the_mirror_names_every_token_rule_one_names():
    """The invariant behind the test above, asserted on the patterns themselves.

    The pair test catches a divergence only for the shapes it enumerates. This one fails the moment
    rule 1 gains ANY token the mirror lacks, which is the actual mistake - twice now.

    Rule 1's own `follow ?-? ?up` alternative is excluded on both sides: the mirror is defined as
    "rule 1 MINUS that token", so its absence there is the point rather than a gap.
    """
    # The rule that CONTAINS the follow-up token, not merely the first one answering 1: four rules
    # map to category 1 and the mirror is defined against this one alone.
    rule_one = next(
        pattern.pattern
        for pattern, category in classification._RULES
        if category == "1" and classification._FOLLOWUP_TOKEN.pattern in pattern.pattern
    )
    mirror = classification._TREATING_VISIT_WITHOUT_FOLLOWUP.pattern
    followup = classification._FOLLOWUP_TOKEN.pattern
    missing = [
        alt for alt in rule_one.split("|") if alt != followup and alt not in mirror.split("|")
    ]
    assert not missing, (
        f"rule 1 names {missing} which _TREATING_VISIT_WITHOUT_FOLLOWUP does not - the two lists "
        "are a pair and move together, see the comment above the mirror"
    )


def test_the_followup_guard_still_yields_where_181_said_it_should():
    """#181 must not be widened by the addition. A follow-up token with NO treating-visit token
    still surrenders to the modality, which is the whole of that change."""
    assert classification.match_rules("Follow-Up MRI") == "3"
    assert classification.match_rules("Deposition Follow-Up") == "9"
    assert classification.match_rules("Follow-Up Operative Report") == "8"
    assert classification.match_rules("Follow-Up Laboratory Results") == "14"
    # ...and one WITH a treating-visit token still keeps 1, which is the other half.
    assert classification.match_rules("Office Visit Follow-Up MRI") == "1"


# The SAME divergence one level up, and the level the mirror cannot reach. `_TREATING_VISIT_TOKENS`
# asks "did rule 1 match on one of its own other tokens" - it says nothing about the THREE other
# rules that answer category 1 (return-to-work voucher #140, emergency-department visit #160,
# History & Physical #161). The demotion filters `matches` by VALUE, so it removed their category 1
# too, and each of those rules names a treating document outright - exactly the case the guard
# exists to protect. Measured over all 1,877 distinct titles on the box: ZERO change answer, so this
# is a statement of intent for the day a title carries both tokens, not a fix for observed loss.
@pytest.mark.parametrize(
    "treating",
    ["Return-to-Work and Voucher Report", "Emergency Department Record", "History and Physical"],
)
@pytest.mark.parametrize("modality", ["MRI of the Lumbar Spine", "Operative Report"])
def test_every_rule_that_names_a_treating_document_keeps_category_1(treating, modality):
    """Asserted as a PAIR against rule 1's own synonym, for the reason the test above gives: what
    must never happen is two titles naming a treating document answering differently."""
    theirs = classification.match_rules(f"{treating} Follow-Up {modality}")
    rule_one = classification.match_rules(f"Progress Report Follow-Up {modality}")

    assert theirs == rule_one, (
        f"{treating!r} surrenders category 1 to {modality!r} where 'Progress Report' keeps it: "
        f"{theirs} vs {rule_one}"
    )


def test_the_guard_covers_every_category_1_rule_including_ones_not_written_yet():
    """DERIVED, so a fourth rule answering category 1 is protected the day it is written.

    Both failures this file records were "a list someone had to remember to grow". Enumerating the
    three rules here would be that mistake a third time, so this walks `_RULES` instead.
    """
    own_reason = [
        pattern.pattern
        for pattern, category in classification._RULES
        if category == "1" and not pattern.search(classification._FOLLOWUP_ONLY)
    ]
    assert len(own_reason) >= 3, "the three rules answering 1 for a reason of their own are gone"

    # Lowercase, because this takes the already-normalised text `match_rules` hands it rather than a
    # raw title - the rules are all lowercase patterns. Passing a real title here silently answered
    # False for every rule, which is what this comment is here to stop happening twice.
    assert classification._answered_for_another_reason("emergency department record", "1") is True
    # The bare token is never a reason of its own - that is the whole premise of the withholding.
    assert classification._answered_for_another_reason("follow-up", "1") is False


# ---------------------------------------------------------------------------------------------
# Orders and prescriptions -> 10, answered by Adam 2026-08-24: "All orders can probably go under the
# RFA category", and for prescriptions "if they are on their own it's probably fine to put it under
# the RFA category since it should be the same information being summarized."
#
# The cost of not having this: on the two most recent builds every prescription answered 100 through
# the cascade (17 rows), and 100 is unchecked for summarization by default - so the reviewer was never
# offered a document type they asked to have summarized.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Prescription",
        "Prescriptions",
        "Prescription - Cyclobenzaprine 10mg",
        "Lab Order",
        "Laboratory Order",
        "Imaging Order",
        "MRI Order",
        "Order for MRI Lumbar Spine",
    ],
)
def test_an_order_or_prescription_is_a_request_for_authorization(title):
    """WHEN a title names an order or a prescription, THE SYSTEM SHALL answer 10."""
    assert classification.match_rules(title) == "10"


def test_an_order_for_a_study_is_not_the_study():
    """THE DEFECT THIS FIXES. "Radiology Order" matched `radiolog` in the imaging rule and answered 3
    - an order FOR a study classified as the study itself, then summarized with the diagnostic prompt
    against a page carrying no findings. Same request-versus-answer confusion category 15 was created
    for: 10 is the treating physician ASKING.
    """
    assert classification.match_rules("Radiology Order") == "10"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("MRI Lumbar Spine w/o Contrast", "3"),
        ("Radiology Report", "3"),
        ("X-Ray Report", "3"),
        ("Radiology Test Results", "3"),
    ],
)
def test_the_study_itself_is_still_a_diagnostic_study(title, expected):
    """The order rule sits ABOVE imaging, so it must not claim the reports imaging owns."""
    assert classification.match_rules(title) == expected


@pytest.mark.parametrize(
    "title",
    ["Pre Op Holding Orders", "Outpatient Service Order Information", "Standing Orders"],
)
def test_a_bare_order_token_is_not_enough(title):
    """No bare `order` token, deliberately. Adam's "all orders" describes TREATMENT orders, and the
    word is far broader on real titles - the box carries "Pre Op Holding Orders" (nursing
    instructions, answering 100 and 14) and "Outpatient Service Order Information" (100 and 4).
    Claiming those for RFA would move ward paperwork into a clinical category on a hedge.
    """
    assert classification.match_rules(title) is None


# ---------------------------------------------------------------------------------------------
# A request FOR a supplemental report is paperwork, not the report. Adam 2026-08-24: "The letter
# requesting a supplemental can probably be skipped, as we are more concerned about the supplemental
# report itself." Before this it answered 100 twice and 12 twice over four occurrences.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Request for Supplemental Report",
        "Letter Requesting Supplemental Report",
        "Request for Supplemental QME Report",
        "Attorney Letter Requesting Supplemental AME Report",
    ],
)
def test_a_request_for_a_supplemental_report_is_paperwork(title):
    assert classification.match_rules(title) == "100"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AME Supplemental Report", "12"),
        ("QME Supplemental Report", "12"),
        ("Supplemental QME Report", "12"),
    ],
)
def test_the_supplemental_report_itself_is_untouched(title, expected):
    assert classification.match_rules(title) == expected


def test_the_request_rule_is_directional():
    """THE EXPENSIVE DIRECTION, pinned. The request token must come BEFORE `supplement`. A real
    medical-legal supplemental can carry both words in the other order - "Supplemental Report in
    Response to Your Request" - and an undirected pattern would send it to 100, which is unchecked
    for summarization, dropping a medical-legal report out of the deliverable entirely.
    """
    assert classification.match_rules("Supplemental Report in Response to Your Request") != "100"
    assert (
        classification.match_rules("Supplemental Report Responding to Your Records Request")
        != "100"
    )


# The relative order of `work status` (-> 1) and the order/prescription rule (-> 10) was raised on
# review of #151: the two have never existed in the same file when either was measured. Measured over
# every title on the box - 1,140 distinct titles, 2,874 rows - 637 carry a category-1 token, 21 carry
# an order token, and ZERO carry both. So the order decides nothing today, and these pin the intent.
#
# The asymmetry is the argument: a progress report or work status report can legitimately mention
# ordering a study, because the order is a line inside the report. A standalone order carries no
# progress-report language at all. So category 1 wins a title holding both, and 10 keeps the titles
# that are only an order.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Work Status Report and MRI Order", "1"),
        ("Progress Report - Lab Order Attached", "1"),
        ("Office Visit with Prescription", "1"),
        # and the reverse: an order on its own is still an order
        ("MRI Order", "10"),
        ("Lab Order", "10"),
        ("Prescription", "10"),
    ],
)
def test_a_report_mentioning_an_order_is_still_the_report(title, expected):
    assert classification.match_rules(title) == expected


# Emergency-department visits. The senior reviewer, 2026-08-25: "emergency department records in
# general should be summarized just like a visit would be. But if it's like a patient demographics
# emergency intake type record then we don't need to summarize that."
#
# Ground truth behind this, independent of that opinion. In a 267-page record the ED visit of
# 02/25/2019 occupies pages 32-64 and reached NO deliverable - one General row, never summarized -
# while the human-written report for that same record carries an entry on 02/25/2019, the only 2019
# date in it. In an earlier segmentation of the SAME PDF the ED visit record sat at General too and a
# reviewer TICKED IT BY HAND to get it into the report. So the rule makes deterministic what a
# reviewer already did manually.
#
# Blast radius measured over all 1,370 distinct titles on the box: 3 titles / 3 rows / 39 pages
# (0.28% of titled pages) change answer, and every one of them previously matched NO rule, so nothing
# deterministic is overridden.
@pytest.mark.parametrize(
    "title",
    [
        "Emergency Department Record",
        "Emergency Department Report",
        "Emergency Department Visit",
        "ED Visit Record - Community Medical Center",
        "EMERGENCY DEPARTMENT RECORD",
    ],
)
def test_an_emergency_department_visit_is_a_treating_report(title):
    assert classification.match_rules(title) == "1"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # The rule sits LAST in the table, so a title naming a specific document type keeps it.
        ("Emergency Department Radiology Report", "3"),
        ("Emergency Department Lab Results", "14"),
        ("ED Lab Results", "14"),
        ("Emergency Department Operative Report", "8"),
    ],
)
def test_a_named_document_type_beats_the_emergency_department(title, expected):
    assert classification.match_rules(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        # The intake carve-out the same reply asks for: these are not visit records.
        "Emergency Department Sign In",
        "ED Nsg Rapid Triage",
        "Emergency Department Registration",
        # Already reaches a shipping category through the cascade with its content delivered, so it
        # is deliberately left where it was rather than moved on a rule.
        "Emergency Department Encounter Note",
        # The word boundary is load-bearing: without it the "ed visit record" alternative matches
        # inside an ordinary past-tense sentence.
        "Reviewed visit record from prior treater",
    ],
)
def test_emergency_intake_paperwork_is_not_ruled_a_treating_report(title):
    assert classification.match_rules(title) != "1"


# History & Physical -> a treating report (#161). Adrian measured that no rule matched it, so the
# cascade re-decided every occurrence and answered it five ways.
#
# The destination is reviewer-CONFIRMED rather than inferred from where rows happen to sit. Matching
# the model's own output against the reviewer's copy on identical page spans, over every H&P row on the
# box: 31 rows the model put at 1 and the reviewer agreed, and **13 rows the model put at 6 (Daily /
# SOAP notes) that a reviewer hand-corrected to 1**, 37 pages. An active correction is much stronger
# evidence than a row merely landing somewhere.
#
# It also contradicts 6 rows a reviewer LEFT at another category (3 at 4, 2 at 11, 1 at 6). Leaving a
# value alone is weaker than correcting it, and determinism is the point of the rule - but it is a real
# cost and should not be hidden.
#
# Blast radius over all 1,363 distinct titles on the box: 49 titles / 50 rows / 148 pages (1.07% of
# titled pages), every one of which previously matched NO rule, so nothing deterministic is overridden.
@pytest.mark.parametrize(
    "title",
    [
        "History & Physical",
        "History and Physical",
        "HISTORY & PHYSICAL",
        "H&P",
        "H & P",
        "Pre-Operative History & Physical",
        "History & Physical Report - Encounter 5",
        "Encounter - Office Visit - H&P",
    ],
)
def test_history_and_physical_is_a_treating_report(title):
    assert classification.match_rules(title) == "1"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # THE ordering constraint. A category-4 rule already matches `outpatient procedure h ?& ?p`,
        # and `h ?& ?p` matches the same substring - so placing the H&P rule any earlier would
        # capture these and silently break a rule that already exists.
        ("Outpatient Procedure H&P", "4"),
        ("Outpatient Procedure H & P", "4"),
        ("GI Outpatient H&P", "4"),
        # An evaluator's own report, or a Permanent & Stationary report, still wins.
        ("QME History & Physical", "13"),
        ("AME History and Physical", "13"),
        ("PR-4 History & Physical", "2"),
        ("Permanent and Stationary History & Physical", "2"),
    ],
)
def test_a_more_specific_document_type_beats_history_and_physical(title, expected):
    assert classification.match_rules(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # `history` alone is never enough - these are not this document. Exact answers rather than a
        # "not 1" assertion, which would pass for the wrong reason as soon as one of them started
        # matching something else.
        ("History of Injury", None),
        ("Past Medical History", None),
        ("Interval History", None),
        # already owned by the category-11 rule, and unaffected
        ("Comprehensive Interval History", "11"),
        # `physical` alone must not reach here either: the therapy rule precedes this one
        ("Physical Therapy Progress Note", "5"),
    ],
)
def test_a_bare_history_or_physical_token_is_not_an_h_and_p(title, expected):
    assert classification.match_rules(title) == expected


# --- a bare follow-up token does not outrank a document type ---------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Follow-Up MRI of the Lumbar Spine", "3"),
        ("X-Ray Follow-Up Right Knee", "3"),
        ("Follow Up Ultrasound", "3"),
        ("Colonoscopy Follow Up", "3"),
        ("Sleep Study Follow-Up", "3"),
        ("EMG/NCS Follow Up Study", "3"),
        ("Laboratory Results Follow Up", "14"),
        ("Operative Report Follow-Up", "8"),
        ("Deposition Follow-Up", "9"),
    ],
)
def test_a_follow_up_study_is_the_study_not_a_treating_report(title, expected):
    """DEMONSTRATES the bug: every one of these answered "1" before.

    "follow-up" says WHEN a visit sits in a course of care, not what the pages ARE - a study, an
    operative report and a lab panel can each be a follow-up. The token sat unqualified in the
    category-1 rule at index 7, ahead of imaging (9), operative (10), deposition (11) and laboratory
    (16), and first match wins. So the rule fired, short-circuited before the embedding and LLM
    stages, and returned confidence='high' with needs_review False - the row summarized with the
    PR-2 treating prompt instead of the diagnostic one, with nothing for a reviewer to catch.

    Same shape as the two defects already fixed here: `laborator` living in rule 3, and "Radiology
    Order" matching `radiolog`.
    """
    assert classification.match_rules(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Follow-Up Visit",
        "Follow Up Office Visit",
        "Follow-Up Evaluation",
        "Follow Up",
        "Progress Report",
        # The precision that makes the guard safe: these name a treating visit OUTRIGHT, so the
        # follow-up token is not the only reason category 1 matched and the modality does not win.
        "Office Visit Follow-Up MRI",
        "Progress Note - MRI Lumbar Spine",
        "PR-2 Follow-Up MRI",
    ],
)
def test_a_follow_up_visit_is_still_a_treating_report(title):
    """GUARDS against over-correcting. Measured on all 1,478 distinct titles on the box: ZERO change
    their answer, and zero carry a follow-up token beside a modality token at all - so this is a
    statement of intent for the day a title carries both, not a fix for observed loss.

    The alternative was measured and rejected: qualifying the token so it only matched "follow-up
    visit" and friends moved 4 real titles from 1 to no-rule-at-all.
    """
    assert classification.match_rules(title) == "1"


def test_stopping_a_run_is_not_reported_as_an_llm_failure(monkeypatch):
    """DEMONSTRATES the bug. `generate_with_retry` raises JobCancelled out of its backoff sleep as a
    cooperative control-flow signal, meant to unwind through the categorize pool to _run. The bare
    `except Exception` caught it, logged "LLM classification failed", and returned None - so
    classify() answered from the embedding alone, and the job log blamed the model for a deliberate
    user action."""
    from app.worker.failures import JobCancelled

    def cancelled(*a, **k):
        raise JobCancelled(3, 170)

    monkeypatch.setattr(classification, "generate_with_retry", cancelled)
    monkeypatch.setattr(classification, "get_genai_client", object)

    with pytest.raises(JobCancelled):
        classification.llm_classify("Progress Report")


def test_a_real_llm_failure_is_still_swallowed(monkeypatch):
    """GUARDS the other direction: an unavailable LLM must stay non-fatal, so the cascade falls back
    to the embedding with needs_review set. Only the cancel signal escapes."""

    def boom(*a, **k):
        raise RuntimeError("vertex is unhappy")

    monkeypatch.setattr(classification, "generate_with_retry", boom)
    monkeypatch.setattr(classification, "get_genai_client", object)

    assert classification.llm_classify("Progress Report") is None


# ---------------------------------------------------------------------------------------------
# An email is administrative however it is titled. `^\s*e-?mail\s*$` matched only a title that IS
# the word, so anything an email was ABOUT fell through - and where that something was an evaluator,
# the bare `ame`/`qme` in rule 13 answered it. Found in a real record on 2026-08-31, delivered:
#
#     Email - QME Report/records   -> 13, summarized with the evaluation checklist
#
# Same defect and the same argument as `(ame|qme|pqme) letter`, fixed there in #119 and missed here.
# The reviewers name "mails" in an excluded-types list, so the destination is not in doubt.
@pytest.mark.parametrize(
    "title",
    [
        "Email",
        "E-mail",
        "EMAIL",
        "Emails",
        "Email Chain",
        "Email from the claims examiner",
        "Email - AME Evaluation",
        "E-mails regarding scheduling",
        # NOT "Email - QME Report/records", which is the row that FOUND this: it carries a document
        # NOUN, so the `_DOCUMENT_NOUN` valve stands the administrative rule down and rule 13
        # answers. Document beats wrapper, pinned by test_evaluation_reports_survive_their_cover_page
        # and deliberately not overridden - see the note on _PAPERWORK_ABOUT_A_DOCUMENT. So this
        # change fixes the family and leaves that one row, which is reported rather than forced.
    ],
)
def test_an_email_is_administrative_however_it_is_titled(title):
    assert classification.match_rules(title) == classification.DEFAULT_ID


# The safety property #119 established for letters, restated for emails: standing the administrative
# rule down is not the same as claiming the category. A title naming a real document type keeps it,
# because the valve withholds ONLY the evaluator mention.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Email - MRI of the Lumbar Spine", "3"),
        ("Email - Operative Report", "8"),
        ("Email - Deposition Transcript", "9"),
        ("Email - Laboratory Results", "14"),
        # A SUPPLEMENTAL QME report is a document type of its own (12), not a bare evaluator
        # mention, so it survives where the plain "QME Report" does not.
        ("Email - Supplemental QME Report", "12"),
    ],
)
def test_a_real_document_type_in_an_email_title_still_wins(title, expected):
    assert classification.match_rules(title) == expected


# The trailing word boundary. Without it `e-?mails?` would swallow "Emailed", turning a progress
# report that happens to have been emailed into paperwork - which is content loss, the direction
# that matters.
@pytest.mark.parametrize(
    ("title", "expected"),
    [("Emailed Progress Report", "1"), ("Emailing Instructions", None)],
)
def test_emailed_is_not_an_email(title, expected):
    assert classification.match_rules(title) == expected


# The anchor. An email is one when the title OPENS with it; a clinical document that mentions email
# further in is not, or "Progress Report - please email the patient" becomes paperwork.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Progress Report - sent by email", "1"),
        ("Operative Report forwarded via e-mail", "8"),
    ],
)
def test_an_email_mentioned_later_in_a_title_is_not_an_email(title, expected):
    assert classification.match_rules(title) == expected


def test_the_paperwork_valve_still_covers_what_it_covered_before():
    """`_PAPERWORK_ABOUT_A_DOCUMENT` gained the email alternative; its original one must still fire.

    That pattern is what stops `_DOCUMENT_NOUN` standing the administrative rules down on the word
    "Report" inside "Declaration of Service of Medical Legal Report" - the case #119 and #122 were
    about - so a regression here would quietly re-open that one.
    """
    assert classification.match_rules("Declaration of Service of Medical Legal Report") == "100"
    assert classification.match_rules("Proof of Service of Medical Legal Report") == "100"


# #222. The senior reviewer's instruction is "Yes, do not include the excerpted medical records"
# (#159), and we mostly obeyed it - but nothing PINNED the bare title, so the cascade re-decided it
# per record and eventually answered differently: two rows on that reviewer's own account reached
# category 1, included, and shipped summaries.
#
# #159 deliberately declined to add a rule and was right about the rule it considered. A SUBSTRING
# match on "excerpt" also claims the qualified forms, and the same reviewer's answer carves those
# out - he wants embedded diagnostic studies summarized. `_DOCUMENT_NOUN` cannot rescue them either,
# because "records" is deliberately absent from it. So the predicate is "the title IS the wrapper and
# nothing else", which is what these anchors express and what the second test below defends.
@pytest.mark.parametrize(
    "title",
    [
        "Medical Records Excerpt",
        "Medical Record Excerpt",
        "MEDICAL RECORDS EXCERPTS",
        "  medical records excerpt  ",
        "Medical Excerpted Records",
        "Excerpted Medical Records",
        "Review of Medical Records",
        "Review of the Medical Records",
    ],
)
def test_a_bare_excerpt_wrapper_is_administrative(title):
    assert classification.match_rules(title) == "100"


# The half the substring rule would have broken. These must NOT be claimed: two of them carry no
# `_RULES` match at all, so an administrative hit would send them to DEFAULT_ID and drop real
# content - a diagnostic study in the first case, which is precisely what the reviewer asked to keep.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Medical Record Excerpt - MRI of Right Knee", "3"),  # the study still wins
        ("Medical Record Excerpt - UCLA Medical Center", None),  # cascade decides
        ("Medical Record Excerpt - ABC Occupational Medical Center", None),
        ("Excerpted Medical Records - Lumbar Spine MRI Report", "3"),
    ],
)
def test_a_qualified_excerpt_is_left_to_the_document_it_names(title, expected):
    assert classification.match_rules(title) == expected


def test_the_records_index_is_unchanged_and_not_claimed_by_these_anchors():
    """`medical excerpted records index` already reaches 100 through the pre-existing
    `records? (request|index)` rule, so the anchors here must neither claim it nor take credit for
    it. Pinned because the issue reported its STORED category as 3 - which is an old build's answer,
    not what the rules say today (the stored-vs-current trap)."""
    assert classification.match_rules("medical excerpted records index") == "100"
    assert classification.match_rules("Index of Records") == "100"
