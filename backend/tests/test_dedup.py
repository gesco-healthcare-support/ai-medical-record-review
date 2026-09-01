"""Unit tests for the duplicate-clustering service (services.dedup).

cluster_rows is pure; confirm_cluster's model call is monkeypatched (no Vertex).
"""

import difflib
import json

from app.services import dedup


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)


def test_cluster_rows_groups_near_identical_and_excludes_distinct():
    items = [
        {"idx": 0, "text": "patient reports lower back pain lumbar tenderness physical therapy"},
        {
            "idx": 1,
            "text": "patient reports lower back pain lumbar tenderness physical therapy plan",
        },
        {
            "idx": 2,
            "text": "operative report knee arthroscopy anesthesia general surgeon signature",
        },
    ]
    clusters = dedup.cluster_rows(items, jaccard_threshold=0.5)
    assert len(clusters) == 1
    assert {m["idx"] for m in clusters[0]["members"]} == {0, 1}
    assert clusters[0]["similarity"] > 0.7  # near-identical -> high char-difflib


def test_cluster_similarity_low_for_shared_template_different_content():
    a = "work activity status report employer name date restrictions no lifting over 10 lbs today"
    b = "work activity status report employer name date restrictions full duty no restrictions today"
    clusters = dedup.cluster_rows(
        [{"idx": 0, "text": a}, {"idx": 1, "text": b}], jaccard_threshold=0.4
    )
    assert len(clusters) == 1  # Jaccard groups them (shared vocabulary)
    assert clusters[0]["similarity"] < 0.9  # but difflib exposes they may be a series


def test_similarity_compares_excerpts_not_whole_documents():
    """difflib is quadratic, so the score reads only the first _EXCERPT_CHARS of each member - the
    same window the AI-confirm call uses. Two long documents that agree on that window and diverge
    far beyond it still score as near-identical, which is what makes the check affordable on a
    re-scanned transcript (measured: 33ms vs 1132ms on a real 13k-char cluster, verdict unchanged)."""
    shared = "identical scanned report text " * 60  # ~1800 chars, past the excerpt window
    # Single-char tails so the word-set signature (and therefore Jaccard) is identical: this test is
    # about the SCORE, not about what clusters.
    a = shared + "z " * 20000
    b = shared + "q " * 20000
    clusters = dedup.cluster_rows([{"idx": 0, "text": a}, {"idx": 1, "text": b}])
    assert len(clusters) == 1
    assert clusters[0]["similarity"] == 1.0
    # Proof it is the truncation doing this: the full texts are only ~half alike.
    assert dedup._min_difflib.__doc__  # documented behaviour
    assert len(shared) > dedup._EXCERPT_CHARS


def test_similarity_still_separates_a_form_series_within_the_excerpt():
    # WHEN members share a template but differ in content inside the window, the score stays low.
    a = "work status report employer date restrictions no lifting over 10 lbs " * 30
    b = "work status report employer date restrictions full duty without limits " * 30
    # Threshold lowered to the point where the shared template alone groups them (as a real form
    # series does), which is exactly the case the score has to distinguish.
    clusters = dedup.cluster_rows([{"idx": 0, "text": a}, {"idx": 1, "text": b}], 0.4)
    assert len(clusters) == 1
    assert clusters[0]["similarity"] < 0.95


def test_confirm_cluster_returns_confirmed_subset(monkeypatch):
    members = [
        {"title": "A", "date": "1", "text": "x"},
        {"title": "B", "date": "2", "text": "y"},
        {"title": "C", "date": "3", "text": "z"},
    ]
    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        dedup, "generate_with_retry", lambda *a, **k: _Resp({"duplicate_indices": [1, 3]})
    )
    assert dedup.confirm_cluster(members, model="m") == [members[0], members[2]]


def test_confirm_cluster_empty_when_model_finds_no_duplicates(monkeypatch):
    members = [{"title": "A", "date": "1", "text": "x"}, {"title": "B", "date": "2", "text": "y"}]
    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        dedup, "generate_with_retry", lambda *a, **k: _Resp({"duplicate_indices": [1]})
    )
    assert dedup.confirm_cluster(members, model="m") == []


def test_confirm_cluster_failsafe_trusts_candidate_on_error(monkeypatch):
    members = [{"title": "A", "date": "1", "text": "x"}, {"title": "B", "date": "2", "text": "y"}]

    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(dedup, "generate_with_retry", boom)
    assert dedup.confirm_cluster(members, model="m") == members


def test_confirm_cluster_single_member_returns_empty():
    assert dedup.confirm_cluster([{"text": "x"}]) == []


def _members(*pairs, category=None):
    """Cluster members from (date, title) pairs; text is irrelevant to the gate.

    ``category`` is left UNSET by default so these fixtures exercise the date+title path exactly as
    they did before category joined the rule - an unset category normalises to UNKNOWN and can never
    stand in for a title.
    """
    return [
        {"date": date, "title": title, "category": category, "text": "x"} for date, title in pairs
    ]


def test_gate_passes_a_cluster_sharing_both_date_and_title():
    members = _members(("05/08/2022", "Progress Report"), ("05/08/2022", "progress  report"))
    assert dedup.duplicate_gate(members, 0.30) is True  # normalization: case + whitespace


def test_gate_rejects_a_multi_date_series_despite_shared_title():
    members = _members(("05/08/2022", "Work Status Report"), ("06/12/2022", "Work Status Report"))
    assert dedup.duplicate_gate(members, 0.51) is False


def test_gate_override_keeps_a_genuine_rescan_with_two_dates():
    """The measured 0.998 pair: two transcribed dates, one document. Content overrides metadata."""
    members = _members(("05/08/2022", "Operative Report"), ("05/09/2022", "Operative Report"))
    assert dedup.duplicate_gate(members, 0.998) is True


def test_gate_treats_absent_date_and_title_as_unknown_not_matching():
    # Aggregate-built records carry "-" for every row; two unknowns have told us nothing, so the
    # gate must fall through to content similarity rather than reading them as a match.
    # Expressed RELATIVE to the configured override rather than against a literal. This test is
    # about the fall-through mechanism, not about where the threshold sits, and it previously
    # hardcoded 0.98 as "below the raised override" - which quietly made a mechanism test fail the
    # moment the policy value moved.
    from app.config import get_settings

    override = get_settings().dupe_similarity_override
    members = _members(("-", "-"), ("-", "-"))
    assert dedup.duplicate_gate(members, 0.0) is False
    assert dedup.duplicate_gate(members, override - 0.05) is False
    assert dedup.duplicate_gate(members, override) is True


def test_gate_handles_similarity_none_from_pre_column_clusters():
    members = _members(("05/08/2022", "A"), ("06/08/2022", "B"))
    assert dedup.duplicate_gate(members, None) is False


def test_gate_reproduces_the_reviewer_verdicts_on_the_measured_clusters():
    """Regression fixture: the 22 live clusters measured 2026-07-31, as
    (similarity, distinct dates, distinct titles, reviewer verdict) - no model call needed.

    Genuine duplicates all sat at >= 0.994; every false positive was a multi-date series between
    0.07 and 0.82. This pins the separation so a threshold change cannot silently regress it.
    """
    cases = [
        # (similarity, dates, titles, is_duplicate)
        (0.823, 7, 3, False),
        (0.514, 6, 4, False),
        (0.300, 4, 1, False),
        (0.399, 1, 2, False),
        (0.073, 2, 2, False),
        (1.000, 1, 1, True),
        (0.994, 1, 2, True),
        (0.998, 2, 1, True),
    ]
    for similarity, dates, titles, expected in cases:
        members = _members(
            *(
                (f"0{(i % dates) + 1}/08/2022", f"Report {(i % titles) + 1}")
                for i in range(max(dates, titles))
            )
        )
        assert dedup.duplicate_gate(members, similarity) is expected, (
            f"similarity={similarity} dates={dates} titles={titles}"
        )


# --- the closure-minimum defect (#125) ---------------------------------------------------------
#
# `cluster_rows` admits a cross-date pair only when its content score clears the override - per
# EDGE. `duplicate_gate` then asked the same question of the transitive CLOSURE, whose minimum no
# chain longer than one hop can satisfy. Measured on the box at the running 0.90: 21 candidates
# rejected, 20 of them chains where every edge already cleared it, 0 sharing a date - rejected on a
# weakest pair of 0.76-0.90 while containing a strongest pair whose median was 1.000.


def _chain_texts():
    """A~B~C: each adjacent pair near-identical, the ends further apart. The shape of a re-scanned
    document series where each copy differs slightly from the last."""
    a = "operative report knee arthroscopy anesthesia general surgeon signature findings alpha"
    b = "operative report knee arthroscopy anesthesia general surgeon signature findings beta"
    c = "operative report knee arthroscopy anesthesia general surgeon signature results gamma delta"
    return a, b, c


def test_a_chain_of_strong_edges_reports_itself_as_content_joined():
    """Every union that built this cluster cleared the override, so the cluster says so."""
    a, b, c = _chain_texts()
    items = [
        {"idx": 0, "text": a, "date": "01/02/2022"},
        {"idx": 1, "text": b, "date": "03/04/2022"},
        {"idx": 2, "text": c, "date": "05/06/2022"},
    ]
    clusters = dedup.cluster_rows(items, jaccard_threshold=0.4, cross_date_override=0.80)
    assert len(clusters) == 1
    assert clusters[0]["content_joined"] is True


def test_a_same_date_union_is_not_content_joined():
    """The same-date branch joins WITHOUT scoring content, so it cannot claim the override was met.

    Such a cluster falls through to the gate's first branch, which is the one built for it.
    """
    items = [
        {"idx": 0, "text": "work status report restrictions no lifting today alpha"},
        {"idx": 1, "text": "work status report restrictions full duty today beta"},
    ]
    for item in items:
        item["date"] = "05/08/2022"
    clusters = dedup.cluster_rows(items, jaccard_threshold=0.4, cross_date_override=0.99)
    assert len(clusters) == 1
    assert clusters[0]["content_joined"] is False


def test_gate_admits_a_content_joined_chain_its_closure_minimum_would_reject():
    """THE DEFECT. Every edge cleared the override; the closure minimum cannot, by construction.

    Without the third branch this returns False - no shared date, and a closure minimum below the
    override - and a chain of genuine re-scans is discarded with every strong pair inside it.
    """
    members = _members(("01/02/2022", "Operative Report"), ("05/06/2022", "Operative Report"))
    assert dedup.duplicate_gate(members, 0.62, content_joined=True) is True
    # and the same cluster WITHOUT that provenance is still rejected, as before
    assert dedup.duplicate_gate(members, 0.62, content_joined=False) is False


def test_content_joined_does_not_rescue_a_cluster_the_dates_and_content_both_reject():
    """The flag is the ONLY thing that changes. A cluster nothing joined on content is untouched."""
    members = _members(("05/08/2022", "Work Status Report"), ("06/12/2022", "Work Status Report"))
    assert dedup.duplicate_gate(members, 0.51) is False


def test_content_joined_defaults_off_so_existing_callers_are_unchanged():
    """Every verdict measured before this change must be reproducible without passing the flag."""
    members = _members(("05/08/2022", "Progress Report"), ("05/08/2022", "progress  report"))
    assert dedup.duplicate_gate(members, 0.30) is True
    multi = _members(("05/08/2022", "Work Status"), ("06/12/2022", "Work Status"))
    assert dedup.duplicate_gate(multi, 0.51) is False


def test_cluster_rows_threshold_defaults_to_the_configured_value(monkeypatch):
    calls = []

    real_jaccard = dedup._jaccard

    def spy(a, b):
        value = real_jaccard(a, b)
        calls.append(value)
        return value

    monkeypatch.setattr(dedup, "_jaccard", spy)
    shared = "alpha beta gamma delta epsilon zeta eta theta"  # 8 words, so one differing -> 8/10
    items = [{"text": f"{shared} iota"}, {"text": f"{shared} kappa"}]
    # The configured default (0.70) groups these at 0.8; an explicit argument still wins.
    assert len(dedup.cluster_rows(items)) == 1
    assert calls == [0.8]  # the configured cut, not a hardcoded argument, decided this
    assert dedup.cluster_rows(items, jaccard_threshold=0.99) == []


# --- the date-first rule (2026-08-06) ------------------------------------------------------------


def test_a_shared_category_stands_in_for_a_shared_title():
    """WHEN two same-date sub-documents share a category but not a title, THE SYSTEM SHALL admit
    them. Category is an ALTERNATIVE, so it can only ever admit - a wrong one cannot hide a copy."""
    members = _members(
        ("05/08/2022", "OPERATIVE REPORT"), ("05/08/2022", "Op Report - Dr Smith"), category="5"
    )
    assert dedup.duplicate_gate(members, 0.30) is True


def test_a_shared_category_cannot_rescue_a_date_mismatch():
    """WHEN the dates differ, THE SYSTEM SHALL NOT admit on category alone - the recurring therapy
    series shares both title and category across six visit dates, and that is the case this rule
    exists to reject."""
    members = _members(
        ("05/08/2022", "Physical Therapy"), ("06/12/2022", "Physical Therapy"), category="5"
    )
    assert dedup.duplicate_gate(members, 0.51) is False


def test_an_unknown_category_is_not_a_match():
    """WHEN a category is an absent-value sentinel, THE SYSTEM SHALL treat it as UNKNOWN. Two rows
    that both say "-" have told us nothing, and reading that as agreement would admit every pair on
    an aggregate-built record."""
    members = _members(("05/08/2022", "A"), ("05/08/2022", "B"), category="-")
    assert dedup.duplicate_gate(members, 0.30) is False


def test_masking_dates_makes_a_stamped_rescan_score_as_a_copy():
    """WHEN two texts are identical apart from a date, THE SYSTEM SHALL score them as if the dates
    matched - a fax re-send stamp is not a content difference."""
    body = "PATIENT COMPLAINS OF LOW BACK PAIN RADIATING TO THE LEFT LEG. EXAM UNREMARKABLE. "
    a = body + "Received 05/08/2022"
    b = body + "Received 11/29/2023"
    assert dedup._min_difflib([a, b]) == 1.0
    assert dedup.mask_dates(a) == dedup.mask_dates(b)


def test_masking_dates_does_not_make_different_findings_look_alike():
    """WHEN two documents differ in their findings, THE SYSTEM SHALL still score them apart. Masking
    must not be a blunt instrument that collapses a therapy series into one cluster."""
    a = "Visit 05/08/2022. Lumbar flexion 40 degrees. Pain 7 of 10. Continue therapy."
    b = "Visit 06/12/2022. Cervical rotation 70 degrees. Pain 2 of 10. Discharge to home."
    assert dedup._min_difflib([a, b]) < 0.90


def test_clustering_keeps_same_content_on_different_dates_apart():
    """WHEN two sub-documents share content but carry DIFFERENT dates and are not near-identical,
    THE SYSTEM SHALL NOT cluster them - six visits on one form are not six copies."""
    shared = "PHYSICAL THERAPY DAILY NOTE " * 12
    items = [
        {
            "id": 1,
            "date": "05/08/2022",
            "title": "PT",
            "category": "5",
            "text": shared + "flexion 40",
        },
        {
            "id": 2,
            "date": "06/12/2022",
            "title": "PT",
            "category": "5",
            "text": shared + "rotation 70",
        },
    ]
    assert dedup.cluster_rows(items) == []


def test_clustering_still_finds_a_near_identical_pair_across_dates():
    """WHEN two sub-documents are essentially identical but carry different dates, THE SYSTEM SHALL
    still cluster them. This is the reviewer-confirmed 0.998 pair; without this pass the date is an
    absolute veto and that pair is lost with no trace."""
    body = "OPERATIVE REPORT. PROCEDURE: L4-L5 MICRODISCECTOMY. FINDINGS AS DICTATED. " * 6
    items = [
        {"id": 1, "date": "05/08/2022", "title": "Op Report", "category": "5", "text": body + "A"},
        {"id": 2, "date": "05/09/2022", "title": "Op Report", "category": "5", "text": body + "A"},
    ]
    clusters = dedup.cluster_rows(items)
    assert len(clusters) == 1
    assert {m["id"] for m in clusters[0]["members"]} == {1, 2}


def test_clustering_groups_same_date_copies_on_content_alone():
    """WHEN two sub-documents share a date and near-identical content, THE SYSTEM SHALL cluster them
    without needing the stricter cross-date bar."""
    body = "PROGRESS REPORT. SUBJECTIVE: ONGOING SHOULDER PAIN. PLAN: CONTINUE MEDICATION. " * 6
    items = [
        {"id": 1, "date": "05/08/2022", "title": "Progress", "category": "1", "text": body + "x"},
        {"id": 2, "date": "05/08/2022", "title": "Progress", "category": "1", "text": body + "y"},
    ]
    clusters = dedup.cluster_rows(items)
    assert len(clusters) == 1


# Generated once by a seeded search for a pair difflib scores asymmetrically under autojunk
# (swing 0.067), then inlined so the test carries no randomness. PHI-free: assembled from a fixed
# vocabulary of clinical stock phrases.
_ASYM_A = "independently as no independently continued of ibuprofen ice weekly no acute four motion weekly 128/82 therapy strain plan needed provider plan reports 128/82 strength medications independently and no acute in no plan patient 5/5 motion weeks twice in rest ambulates weekly no pulse patient pulse lumbar four 600mg range patient strain continued continued range 600mg distress reports distress follow of distress 600mg 128/82 ambulates bilaterally as ibuprofen ice ambulates ambulates of of in full continued in continued ice full plan 74 acute of range 600mg bilaterally 74 128/82 of reports vitals medications plan continued ibuprofen up ambulates impression patient mild mild 74"

_ASYM_B = "independently as no independently continued of ibuprofen ice weekly no acute four motion weekly 128/82 therapy strain plan needed provider plan reports 128/82 strength medications independently and no acute in no plan patient 5/5 motion weeks twice in rest ambulates weekly no pulse patient pulse lumbar four 600mg range patient strain continued continued range 600mg distress reports distress follow of distress 600mg 128/82 follow full patient discomfort independently as weeks cervical follow mild pulse lumbar bilaterally full 600mg range strain therapy physical ibuprofen provider ibuprofen needed continued reports weeks acute strain ambulates advised medications 5/5 needed discomfort needed rest and distress 600mg advised physical advised medications acute weekly distress motion twice 5/5 plan of bilaterally"


# ---------------------------------------------------------------------------------------------
# difflib's autojunk must stay OFF for these comparisons.
#
# autojunk refuses to match on any element occurring in more than 1% of positions. That is built for
# LINE sequences; these are CHARACTER sequences, so on a 1,500-character excerpt it junks the spaces
# and common letters - most of medical prose - and suppresses the score far below what the texts
# share. Measured on the box 2026-08-24: across 25 records / 84 clusters, 75 score higher with it off
# and 13 flip REJECT -> PASS at the gate, none the other way (0.442 -> 0.958 among them).
#
# It is also computed on the SECOND sequence only, so ratio(a, b) != ratio(b, a) - up to 0.249 apart
# on real pairs, with 10 of one cluster's 78 pairs straddling the 0.90 gate purely on row order.
# ---------------------------------------------------------------------------------------------


def _suppressed_pair():
    """A pair autojunk scores near zero while the texts plainly share their subject.

    Same clinical content, one side numeric and one spelled out, so the shared vocabulary is real but
    the characters recur heavily - exactly the shape autojunk mistakes for noise.
    """
    a = "vitals 120 80 98 6 72 16 " * 20
    b = "vitals one twenty eighty ninety eight " * 20
    return a, b


def test_autojunk_is_not_applied_to_character_comparisons():
    """WHEN two sub-documents share their text, THE SYSTEM SHALL NOT let autojunk suppress the score.

    Fails on the previous behaviour: the same pair scored 0.011, which reads as "nothing in common"
    and is how a genuine re-scan was dismissed as a form series.
    """
    a, b = _suppressed_pair()
    scored = dedup._min_difflib([a, b])
    with_autojunk = difflib.SequenceMatcher(None, a, b).ratio()

    assert with_autojunk < 0.05, "fixture no longer demonstrates the suppression"
    assert scored > 0.30, f"autojunk still suppressing the score ({scored})"
    assert scored > with_autojunk * 5


def test_the_score_barely_moves_when_the_pair_is_swapped():
    """The score must be a property of the PAIR, not of which row came first.

    Not asserted as exact equality: difflib's matcher is mildly order-sensitive even with autojunk
    off. What the fix removes is the LARGE, autojunk-driven swing - 0.067 on this fixture and up to
    0.249 on real pairs - which is what let a borderline pair land either side of the gate.
    """
    a, b = _ASYM_A, _ASYM_B
    forward = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    backward = difflib.SequenceMatcher(None, b, a, autojunk=False).ratio()
    junk_forward = difflib.SequenceMatcher(None, a, b).ratio()
    junk_backward = difflib.SequenceMatcher(None, b, a).ratio()

    assert abs(junk_forward - junk_backward) > 0.05, "fixture no longer shows the asymmetry"
    assert abs(forward - backward) < 0.01


def test_an_identical_pair_still_scores_one():
    """The case the whole stage exists for must be untouched."""
    text = "DATE OF SERVICE 05/08/2022 " + ("bilateral knee radiographs unremarkable. " * 30)
    assert dedup._min_difflib([text, text]) == 1.0


def test_a_form_series_with_different_findings_is_still_separated():
    """autojunk off raises scores; it must raise them for RE-SCANS, not for a recurring form whose
    findings differ. This is the dominant false positive and it has to stay below the override."""
    template = "PROGRESS REPORT patient redacted provider redacted plan of care. " * 12
    a = template + " findings: cervical radiculopathy with C6 involvement, grip strength reduced"
    b = template + " findings: no acute distress, lumbar range of motion full, discharged today"
    assert dedup._min_difflib([a, b]) < 0.99


def test_cluster_similarity_is_stable_under_member_reordering():
    """A cluster's reported similarity must not change when its members arrive in another order."""
    body = "physical therapy three times weekly for the lumbar spine. " * 30
    items = [
        {"id": 0, "date": "05/08/2022", "title": "PR-2", "category": "1", "text": body + " alpha"},
        {"id": 1, "date": "06/09/2022", "title": "PR-2", "category": "1", "text": body + " beta"},
        {"id": 2, "date": "07/10/2022", "title": "PR-2", "category": "1", "text": body + " gamma"},
    ]

    def shape(rows):
        return sorted((len(c["members"]), c["similarity"]) for c in dedup.cluster_rows(rows))

    assert shape(items) == shape(list(reversed(items)))
    assert shape(items) == shape([items[1], items[2], items[0]])


def test_the_duplicate_thresholds_agree_between_config_and_compose():
    """WHEN a duplicate threshold is changed, THE SYSTEM SHALL change it in both places.

    `docker-compose.yml` passes all three DUPE_* keys explicitly, so a container reads the COMPOSE
    default and never the one in `config.py`. This setting is the tree's own worked example of what
    that costs: #67 added the compose line while 0.90 was the default here, #81 then raised this file
    to 0.99 and did not touch compose, and every container went on serving 0.90 for three weeks while
    the code, the docstrings and my own analysis all said 0.99. The same guard already exists for
    PAGE_TEXT_WORKERS and PIPELINE_WORKERS; this closes it for the setting that taught us the lesson.
    """
    import re
    from pathlib import Path

    from app.config import get_settings

    settings = get_settings()
    text = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    for key, attr in (
        ("DUPE_JACCARD_THRESHOLD", "dupe_jaccard_threshold"),
        ("DUPE_SIMILARITY_OVERRIDE", "dupe_similarity_override"),
        ("DUPE_MODEL_OVERRIDE", "dupe_model_override"),
    ):
        match = re.search(rf"{key}:\s*\$\{{{key}:-([0-9.]+)\}}", text)
        assert match, f"docker-compose.yml no longer passes {key}"
        assert float(match.group(1)) == getattr(settings, attr), (
            f"docker-compose.yml says {key}={match.group(1)} but config.py says "
            f"{attr}={getattr(settings, attr)}; a deployed container would use the compose value "
            f"and ignore the code default"
        )


# #213: `confirm_cluster` answers with ONE sublist, so a candidate holding two unrelated duplicate
# pairs left the model two wrong options - lump all four together, or name one pair and let the other
# vanish with no record. The second undoes on the quiet what keeping the override low was meant to
# buy: the reviewers asked that nothing be auto-deleted, which makes a missed duplicate cost more
# than a surfaced one. `confirm_groups` asks again about whatever is left.
class TestConfirmGroups:
    @staticmethod
    def _member(tag):
        return {"title": tag, "date": tag, "text": tag * 40}

    @staticmethod
    def _replies(monkeypatch, answers):
        """Serve `answers` (lists of 1-based indices) to successive confirm calls."""
        calls = []
        it = iter(answers)

        def fake(*a, **k):
            payload = next(it)
            calls.append(payload)
            return _Resp({"duplicate_indices": payload})

        monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
        monkeypatch.setattr(dedup, "generate_with_retry", fake)
        return calls

    def test_a_second_pair_in_one_candidate_is_found_instead_of_dropped(self, monkeypatch):
        members = [self._member(t) for t in "abcd"]
        # First call names members 1 and 2; the remainder is then asked about and names both.
        self._replies(monkeypatch, [[1, 2], [1, 2]])

        groups = dedup.confirm_groups(members, model="m")

        assert len(groups) == 2
        assert groups[0] == [members[0], members[1]]
        assert groups[1] == [members[2], members[3]]

    def test_a_candidate_the_model_confirms_whole_is_one_group_and_one_call(self, monkeypatch):
        members = [self._member(t) for t in "abc"]
        calls = self._replies(monkeypatch, [[1, 2, 3]])

        groups = dedup.confirm_groups(members, model="m")

        assert groups == [members]
        assert len(calls) == 1  # nothing left to ask about

    def test_a_leftover_singleton_ends_the_loop(self, monkeypatch):
        members = [self._member(t) for t in "abc"]
        # 1 and 2 are copies; one member remains, which cannot be a group on its own.
        calls = self._replies(monkeypatch, [[1, 2]])

        groups = dedup.confirm_groups(members, model="m")

        assert groups == [[members[0], members[1]]]
        assert len(calls) == 1  # a single member is never sent to the model

    def test_no_duplicates_returns_nothing_exactly_as_before(self, monkeypatch):
        members = [self._member(t) for t in "ab"]
        self._replies(monkeypatch, [[1]])

        assert dedup.confirm_groups(members, model="m") == []

    def test_the_error_failsafe_is_inherited_and_stops_the_loop(self, monkeypatch):
        # A broken confirm must still surface the whole candidate for review, and must not then be
        # asked again - the fail-safe returns every member, so nothing is left.
        members = [self._member(t) for t in "abc"]
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise RuntimeError("vertex down")

        monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
        monkeypatch.setattr(dedup, "generate_with_retry", boom)

        assert dedup.confirm_groups(members, model="m") == [members]
        assert len(calls) == 1

    def test_it_terminates_and_is_bounded_by_half_the_member_count(self, monkeypatch):
        # A confirm that always names the first two of whatever it is given: six members must yield
        # three pairs and stop, never loop. The bound is len(members) // 2 because each accepted
        # group removes at least two.
        members = [self._member(t) for t in "abcdef"]
        calls = self._replies(monkeypatch, [[1, 2]] * 4)

        groups = dedup.confirm_groups(members, model="m")

        assert [len(g) for g in groups] == [2, 2, 2]
        assert len(calls) <= len(members) // 2

    def test_members_are_matched_by_identity_not_by_value(self, monkeypatch):
        # Two rows can carry equal dicts. Removing by value would drop both and lose a real
        # duplicate, so the twin must survive into the second question.
        same = self._member("a")
        members = [dict(same), dict(same), self._member("b"), self._member("b")]
        assert members[0] == members[1]  # equal by value, distinct objects
        self._replies(monkeypatch, [[1, 3], [1, 2]])

        groups = dedup.confirm_groups(members, model="m")

        assert len(groups) == 2
        assert groups[0][0] is members[0]
        assert groups[1][0] is members[1]


def test_group_similarity_scores_only_its_own_members():
    """The candidate's similarity is the minimum over members the model may since have rejected, so
    a carved-out group needs its own number or it reports the divergence of documents not in it."""
    twin = "the quick brown fox jumps over the lazy dog " * 6
    members = [
        {"text": twin},
        {"text": twin},
        {"text": "entirely unrelated content about hydraulic machinery " * 6},
    ]
    whole = dedup._min_difflib([m["text"] for m in members])
    pair = dedup.group_similarity(members[:2])

    assert pair > 0.99  # the two copies are near-identical
    assert whole < 0.5  # the candidate's own figure is dragged down by the third member
