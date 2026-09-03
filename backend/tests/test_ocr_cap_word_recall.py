"""The word-recall instrument (#237): does it measure content loss, or does it measure reflow?

`ocr_max_long_edge_px` is off because a 4.2x speedup came with "6.0% of characters lost" and nothing
could say whether those characters were content. Every test here exists because a plausible
alternative metric would pass the same page and reach the opposite conclusion:

  - reflow, case and punctuation must score a perfect 1.0, because difflib scored those at ~70% and
    that is precisely why it could not be used;
  - a page whose repeated values were thinned must score BELOW 1.0, because a word SET scores it at
    1.0 and the earlier text-layer work used a set;
  - a page whose character volume matches while its words do not must score low, because character
    volume read 96-101% on records whose word recall was far worse, and that single mistake is what
    made a dead end look like a 50x win.

So these are not arithmetic checks on a formula. They are the discriminations the instrument was
commissioned to make, one test each.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from ocr_cap_word_recall import (  # noqa: E402
    _FLOORS,
    _shape,
    binding_pages,
    dpi_for_edge,
    score_page,
    spread,
    summarize_arm,
    tokens,
)

from app.services import ocr  # noqa: E402


def test_the_instrument_and_production_agree_on_the_dpi_a_cap_produces(monkeypatch):
    """`dpi_for_edge` must reproduce `ocr._dpi_for_page`, driven rather than modelled.

    The instrument decides which pages are even worth scoring from this arithmetic. If it drifts
    from production's, the run reports recall for renders production would never make - so the
    numbers would be real and the conclusion wrong, which is the worst failure available to a
    measurement tool. Pinned by calling the real function, so changing production's formula fails
    here instead of silently invalidating a future run.
    """
    settings = ocr.get_settings()
    base = int(settings.ocr_base_dpi)
    edges = (2700.0, 792.0, 1224.0, 5000.0)
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: edges)

    for cap in (0, 3500, 6500, 100000):
        monkeypatch.setattr(settings, "ocr_max_long_edge_px", cap)
        for page, edge in enumerate(edges, start=1):
            assert dpi_for_edge(edge, cap, base) == ocr._dpi_for_page("ignored.pdf", page), (
                f"cap {cap}, page {page}: the instrument and production disagree"
            )


def test_a_cap_does_not_bind_on_an_ordinary_page_so_it_is_not_scored():
    """Only pages some arm actually lowers are scored.

    A US Letter page is 792pt, and 3500 * 72 / 792 is 318 DPI - above the 200 base, so the cap
    changes nothing there. Including such pages would add a guaranteed recall of 1.0 per page to
    every arm, which flatters the worst cap most: that is the dilution that let character volume
    read 96-101% on records that had genuinely lost words.
    """
    edges = [792.0, 2700.0, 792.0, 792.0, 5000.0]
    plan = binding_pages(edges, [3500], base=200)

    assert sorted(plan) == [2, 5], "only the oversized pages should be in the plan"
    assert plan[2][3500] == 93
    assert plan[5][3500] == 50
    assert binding_pages([792.0] * 10, [3500], base=200) == {}


def test_capping_disabled_binds_on_nothing():
    """A cap of 0 is the disabled state, so it must plan no pages at any page size."""
    assert binding_pages([2700.0, 5000.0, 792.0], [0], base=200) == {}


def test_reflow_case_and_punctuation_are_not_losses():
    """The discrimination difflib could not make.

    Same words, re-ordered lines, different case, different punctuation and different whitespace:
    nothing has been lost, so recall, precision and set recall must all be exactly 1.0. difflib
    scores this very page 0.380 - see the head-to-head test below.
    """
    reference = "Cervical spine MRI\nImpression: mild disc bulge\nNo fracture"
    candidate = "no fracture.  IMPRESSION -- mild disc bulge\n\ncervical spine mri"

    score = score_page(reference, candidate)

    assert score["recall"] == 1.0
    assert score["precision"] == 1.0
    assert score["set_recall"] == 1.0
    assert score["lost"]["n"] == 0
    assert score["gained"]["n"] == 0


def test_thinned_repeats_are_a_loss_that_a_word_set_would_score_as_perfect():
    """Why the metric counts a multiset and not a set.

    A results table listing `normal` five times still contains the word `normal` after four of its
    rows have gone, so a set-based recall calls this page perfect. It is not: four values are gone
    out of a medical record. `set_recall` is reported precisely so this gap is visible rather than
    being the whole answer.
    """
    reference = "glucose normal sodium normal calcium normal chloride normal protein normal"
    candidate = "glucose normal sodium calcium chloride protein"

    score = score_page(reference, candidate)

    assert score["set_recall"] == 1.0, "a word set sees no loss here - that is the point"
    assert score["recall"] < 0.75
    assert score["lost"]["n"] == 4


def test_matching_character_volume_does_not_rescue_lost_words():
    """The trap this instrument replaces, stated as a test.

    Character volume agreed 96-101% on records whose word recall was far worse. So a candidate is
    built to hold almost exactly the reference's character count while sharing few of its words: the
    old metric passes it, and word recall must not.
    """
    reference = "flexion twenty degrees extension fifteen degrees rotation thirty degrees"
    candidate = "flexionnn twentyy degrres extensionn fifteenn degrres rotationn thirtyy degres"

    score = score_page(reference, candidate)

    assert 0.9 <= score["char_ratio"] <= 1.15, "volume agrees, which is the premise of the trap"
    assert score["recall"] < 0.35, "the words do not, and that is what must be reported"


def test_the_old_metrics_get_both_pages_backwards_and_this_one_gets_both_right():
    """The head-to-head that justifies building a new instrument at all.

    Two pages, and the two metrics already available in the tree answer both of them wrongly - not
    imprecisely, but with the sign reversed:

        page              chars  difflib   word recall   truth
        reflowed only      1.16    0.380         1.000   nothing lost
        every word broken  1.083   0.920         0.000   everything lost

    So on this pair difflib rates the intact page at 0.38 and the destroyed one at 0.92, and
    character volume rates both at roughly parity. Either would have chosen a cap by preferring the
    page that lost all its words. Measured here rather than asserted from the config comment's
    historical "near 70% at DPI 135", which is a different measurement on different data.
    """
    import difflib

    intact_ref = "Cervical spine MRI\nImpression: mild disc bulge\nNo fracture"
    intact_cand = "no fracture.  IMPRESSION -- mild disc bulge\n\ncervical spine mri"
    broken_ref = "flexion twenty degrees extension fifteen degrees rotation thirty degrees"
    broken_cand = "flexionnn twentyy degrres extensionn fifteenn degrres rotationn thirtyy degres"

    intact_difflib = difflib.SequenceMatcher(None, intact_ref, intact_cand).ratio()
    broken_difflib = difflib.SequenceMatcher(None, broken_ref, broken_cand).ratio()
    intact = score_page(intact_ref, intact_cand)
    broken = score_page(broken_ref, broken_cand)

    # difflib ranks the two pages the wrong way round.
    assert intact_difflib < 0.5 < broken_difflib
    # Character volume cannot tell them apart at all.
    assert abs(intact["char_ratio"] - broken["char_ratio"]) < 0.15
    # Word recall separates them completely, and in the right direction.
    assert intact["recall"] == 1.0
    assert broken["recall"] == 0.0


def test_precision_catches_a_render_that_invented_tokens():
    """Both directions, because measuring one is the documented mistake.

    A lower DPI can read speckle as text as well as drop letters. Recall alone cannot see that, so a
    candidate that kept every reference word and added a page of noise would score a clean 1.0.
    """
    reference = "impression mild degenerative change"
    candidate = "impression mild degenerative change a f rn i1 l1 ee tt"

    score = score_page(reference, candidate)

    assert score["recall"] == 1.0, "nothing was lost"
    assert score["precision"] < 0.6, "but half of what came back was not in the reference"
    assert score["gained"]["mean_len"] < 3, "and its shape says speckle rather than content"


def test_a_blank_reference_page_has_nothing_to_lose():
    """A page the uncapped render read as blank scores 1.0, not 0.0.

    Otherwise every genuinely blank page in a record lands in the alarm bucket and the below-floor
    counts stop meaning anything. Verified against a real observation: one 134-page record has
    exactly one page with `extract_ok=true` and `char_count=0`.
    """
    assert score_page("", "")["recall"] == 1.0
    assert score_page("   \n\n", "anything at all")["recall"] == 1.0


def test_a_candidate_that_returned_nothing_is_the_alarm_working():
    """Recall 0.0 for an empty candidate against a real reference - the loudest case."""
    score = score_page("cervical spine mri impression", "")

    assert score["recall"] == 0.0
    assert score["precision"] == 1.0, "it invented nothing; recall is the number that matters here"
    assert score["lost"]["n"] == 4


def test_shape_reports_numbers_and_never_the_words():
    """PHI safety by construction, not by the caller remembering.

    Page text is PHI, and the exclusive-word report is the one place a naive implementation would
    print it to be helpful. `_shape` returns three numbers, so there is no shape of it that leaks a
    word - and the numbers are chosen to separate speckle (short, not purely alphabetic) from
    genuine content.
    """
    shape = _shape(Counter(["hypertension", "diabetes"]))

    assert set(shape) == {"n", "mean_len", "alpha_share"}
    assert not any(isinstance(v, str) for v in shape.values())
    assert shape["n"] == 2
    assert shape["alpha_share"] == 1.0

    speckle = _shape(Counter(["i1", "rn", "f"]))
    assert speckle["mean_len"] < shape["mean_len"]
    assert speckle["alpha_share"] < 1.0


def test_shape_counts_repeats_rather_than_distinct_words():
    """`n` is a word count, not a vocabulary size - four lost values are four, not one."""
    assert _shape(Counter({"normal": 4}))["n"] == 4


def test_the_summary_leads_with_the_worst_page_not_the_mean():
    """The measurement that blocked this setting averaged well and contained a page at 0.41.

    A mean over pages is the wrong summary for a concentrated failure, so `worst` and the
    below-floor counts must carry it. This set has a mean above 0.90 and a page at 0.41.
    """
    scores = [
        {"recall": 1.0, "precision": 1.0, "ref_words": 100, "lost": {"n": 0}, "gained": {"n": 0}},
        {"recall": 1.0, "precision": 1.0, "ref_words": 100, "lost": {"n": 0}, "gained": {"n": 0}},
        {"recall": 0.41, "precision": 0.9, "ref_words": 100, "lost": {"n": 59}, "gained": {"n": 2}},
    ]

    row = summarize_arm(scores)

    assert row["mean"] > 0.80, "the mean looks acceptable, which is the problem"
    assert row["worst"] == 0.41
    assert row["below_0.7"] == 1
    assert row["below_0.9"] == 1
    assert row["below_0.98"] == 1
    assert row["lost_words"] == 59
    assert row["gained_words"] == 2


def test_the_summary_counts_a_page_against_every_floor_it_falls_below():
    """Cumulative, not bucketed: one page at 0.41 is below all three floors, not just the lowest."""
    row = summarize_arm(
        [{"recall": 0.41, "precision": 1.0, "ref_words": 10, "lost": {"n": 6}, "gained": {"n": 0}}]
    )

    assert [row[f"below_{f}"] for f in _FLOORS] == [1, 1, 1]


def test_pooled_recall_and_the_per_page_mean_disagree_when_pages_differ_in_size():
    """Both are reported because they answer different questions.

    A dense page losing a tenth of its words and a sparse page losing all of them produce the same
    per-page mean as two pages losing a little each - and very different pooled figures. Quoting one
    number here is how "6% of characters" came to mean nothing.
    """
    scores = [
        {"recall": 1.0, "precision": 1.0, "ref_words": 990, "lost": {"n": 0}, "gained": {"n": 0}},
        {"recall": 0.0, "precision": 1.0, "ref_words": 10, "lost": {"n": 10}, "gained": {"n": 0}},
    ]

    row = summarize_arm(scores)

    assert row["mean"] == 0.5, "half the pages failed"
    assert row["pooled_recall"] == 0.99, "and 99% of the words survived"


def test_an_empty_arm_summarizes_without_dividing_by_zero():
    """An arm that bound on no page reports zero pages rather than raising."""
    assert summarize_arm([]) == {"pages": 0}


def test_the_sample_is_spread_across_the_document_and_is_deterministic():
    """Spread rather than the first N, and repeatable.

    The front matter of these records is registration paperwork, so the first N pages are not
    representative of the clinical pages behind them. Determinism is what makes two runs of the same
    document comparable at all.
    """
    candidates = list(range(1, 101))

    picked = spread(candidates, 5)

    assert picked == spread(candidates, 5), "two runs must score the same pages"
    assert len(picked) == 5
    assert max(picked) > 60, "the tail of the document must be represented"
    assert picked != candidates[:5]
    assert spread(candidates, 0) == []
    assert spread([], 5) == []
    assert spread([7, 3], 5) == [3, 7], "fewer candidates than asked for returns them all, sorted"


def test_tokens_keeps_repeats_and_drops_case_and_punctuation():
    assert tokens("Normal, normal; NORMAL") == ["normal", "normal", "normal"]
    assert tokens(None) == []
    assert tokens("T2-weighted") == ["t2", "weighted"]
