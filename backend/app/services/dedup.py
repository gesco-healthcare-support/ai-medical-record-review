"""Duplicate-document clustering for the pre-summarize Duplicates review (problem #1).

Two steps:
- `cluster_rows` groups sub-documents whose OCR text is lexically near-identical (word-set Jaccard
  union-find - the cheap/instant/free candidate finder), and attaches a char-level difflib
  `similarity` per cluster so true re-scans (high) are distinguishable from a recurring form series
  that merely shares boilerplate (low). Pure - no I/O.
- `confirm_cluster` asks the cheapest model to adjudicate which candidate members are copies of the
  SAME document (vs different visits sharing a template). Text-only; no page images.
"""

import difflib
import itertools
import json
import logging
import re

from google.genai import types

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 1500

CONFIRM_PROMPT = (
    "You are given several scanned sub-documents from ONE medical record that share similar text. "
    "Decide which of them are copies or re-scans of the SAME underlying document (same encounter and "
    "content - even if a cover/first page differs or scan quality varies), as opposed to DIFFERENT "
    "documents that merely share a form template or boilerplate (e.g. the same visit form filled out "
    "on different dates, with different findings). Return the 1-based indices that are copies of the "
    "same document. If they are all distinct documents, return an empty list."
)

_CONFIRM_SCHEMA = {
    "type": "OBJECT",
    "properties": {"duplicate_indices": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
    "required": ["duplicate_indices"],
}


# Date grammar, reused from summary_doi's rather than written a third time: slash/dot/dash separated,
# 1-2 digit month and day, 2-4 digit year. Masking these out is what stops a stamped or handwritten
# date - a fax re-send line, a received stamp - from making two scans of ONE document score lower than
# they should. It cannot make a recurring therapy series look alike, because those differ in their
# findings, not merely in their dates.
_DATE_LIKE = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b")
_DATE_MASK = "DATEMASK"


def mask_dates(text):
    """``text`` with date-shaped tokens replaced by a fixed placeholder.

    Applied to BOTH the candidate finder and the similarity score, so the two always agree on what
    they are comparing. The placeholder is a word rather than an empty string, so removing a date
    cannot run its neighbours together into a token present in neither document.
    """
    return _DATE_LIKE.sub(_DATE_MASK, text or "")


def _sig(text):
    return set(re.findall(r"[a-z0-9]{2,}", mask_dates(text).lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _find(parent: list[int], x: int) -> int:
    """Union-find root of ``x`` in ``parent``, compressing the path it walks.

    Module-level rather than a closure because `cluster_rows` keeps two of these structures over
    the same rows and they must not diverge in behaviour.
    """
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _min_difflib(texts):
    """The lowest pairwise char-level ratio across ``texts`` (1.0 for a single item). Low = the
    members diverge in content (likely a recurring-form series), high = near-identical re-scans.

    Compares only the first ``_EXCERPT_CHARS`` of each member - the same window the AI-confirm call
    reads. difflib is quadratic in length AND in member count, so full text costs minutes on a
    re-scanned transcript. Measured on a real 6-member cluster (13k chars each): 0.219 truncated vs
    0.068 full, 33ms vs 1132ms - the verdict (a form series, nowhere near a true copy's 1.000) is
    unchanged at 34x less cost.

    ``autojunk=False`` is REQUIRED here, not a preference. difflib's autojunk heuristic treats any
    element occurring in more than 1% of positions as junk and refuses to match on it. That is built
    for LINE sequences, where a line repeated throughout a file really is noise. These are CHARACTER
    sequences, so on a 1,500-character excerpt it junks every character appearing more than ~15
    times - the spaces and the common letters, which is most of medical prose. The result is a
    similarity score suppressed far below what the texts actually share.

    Measured on the box 2026-08-24 over every stored record. Across 25 records / 84 candidate
    clusters, **75 clusters score higher with autojunk off and 13 flip from REJECT to PASS at the
    gate, none the other way** - among them 0.442 -> 0.958, 0.431 -> 0.909 and 0.514 -> 0.931, which
    are near-identical re-scans the gate was dismissing as a recurring form series. Recall is the
    direction that costs content here: a missed duplicate ships two near-identical paragraphs to a
    client with nothing on screen to hint at it.

    A second symptom showed the score was not even a well-defined property of a PAIR: autojunk is
    computed on the SECOND sequence only, so ``ratio(a, b) != ratio(b, a)``. On the 78 pairs of one
    real 13-member cluster, swapping the arguments moved a score by up to **0.249** (0.894 one way,
    0.645 the other) and 10 of those pairs straddled the 0.90 gate - so whether two sub-documents
    counted as candidate copies turned on which of them held the lower ``idx``.

    THIS FILE PREVIOUSLY CLAIMED "turning autojunk off collapsed that swing to 0.000 on every pair
    measured", AND THAT WAS WRONG - true of the one cluster it was measured on, false in general.
    Autojunk was *a* cause and disabling it removed that one. A second cause is structural and
    survives it: ``find_longest_match`` returns the maximal block that "starts earliest in a, and of
    all those ... starts earliest in b" (CPython ``difflib.py``), a tie-break that ranks position in
    ``a`` above position in ``b``. Swap the arguments and a different block can win the tie, which
    changes the whole recursive decomposition and the matched total ``M``; ``ratio()`` is ``2M/T``
    and only ``T`` is symmetric. With autojunk already off, ``ratio("aba", "babba")`` is 0.750 and
    ``ratio("babba", "aba")`` is 0.500. `test_dedup.py` knew this - its swap test asserts
    ``< 0.01`` rather than equality - while this docstring said the opposite, and the contradiction
    is why nobody looked again for a month.

    Ties need repeated substrings, so it is REPETITION that decides whether this bites, and the
    13-member cluster measured had little. Across three synthetic corpora: distinct clinical prose
    0.1% of pairs asymmetric and no gate verdict affected, two scans of one form 0.0%, and a
    recurring FORM SERIES - the dominant real false positive, boilerplate-heavy and scoring
    0.869-0.982 astride the gate - **90% asymmetric with 12.75% of gate verdicts decided by row
    order alone**. Real duplicates score 0.916-1.000 and are untouched, so what row order decided
    was whether a false positive cost a confirm call, never whether a duplicate was found.

    FIXED by sorting the excerpts below, which pins the argument order. Note what that does and does
    not do: difflib remains asymmetric, and this function stops exposing it. Sorting was chosen over
    max/min/mean of both directions because it costs 1.01x rather than 2.36x on roughly half of
    dedup's runtime, and because `config.py` establishes this score does not separate duplicates
    from form series at any threshold - so paying to shift it buys nothing measurable.

    THE RE-DERIVATION WAS DONE, 2026-08-25, and it says the threshold cannot be derived at all. See
    the note on `dupe_similarity_override` in `config.py`. In short: on the corrected scale, reviewer-
    dismissed clusters run 0.509 to 1.000 and reviewer-kept clusters 0.529 to 1.000, so there is no
    separation to cut at any value.

    WIDENING THIS WINDOW WAS PROPOSED AND REJECTED, on measurement. It looked like the cause of the
    overlap, because 34 of the 39 reviewer-labelled clusters have members longer than 1,500 characters.
    Recomputed at 1500 / 3000 / 6000 / full text over those clusters, the false-positive ceiling only
    falls 1.000 -> 0.995 and the duplicate floor only rises 0.529 -> 0.554: **the overlap survives
    every window**, so the excerpt is not what stops similarity discriminating.

    And the cost is real. Measured over all 82 clusters with stored text on the box:

        window    total    mean/cluster    worst single cluster
          1500     5.3s        65ms              0.8s
          3000    18.4s       224ms              2.9s
          6000    51.9s       633ms             10.8s
          full   142.4s      1736ms             27.5s      <- 27x

    A whole dedup job on the 229-page record runs in 10-12 seconds end to end, so one full-text
    cluster would more than double it. The one thing widening buys is a single false positive falling
    from 1.000 to 0.918 - and it only reaches 0.995 at 3,000 characters, which still passes the 0.99
    gate. So the verdict change costs 27x, not 3.5x, for one cluster. 1500 stays.
    """
    # SORTED, and that is the whole of the argument-order fix. `ratio()` is not symmetric (see the
    # tie-break note above), and `combinations` preserves input order, so without this every pair
    # reaches the matcher in whatever order its rows arrived and scores accordingly. Sorting pins
    # the smaller text as `a` for every pair, which is what makes the result a property of the
    # texts. `min` over the pairs is an aggregate, so ordering the list cannot change anything else.
    excerpts = sorted(mask_dates(text)[:_EXCERPT_CHARS] for text in texts)
    ratios = [
        difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        for a, b in itertools.combinations(excerpts, 2)
    ]
    return round(min(ratios), 3) if ratios else 1.0


def cluster_rows(items, jaccard_threshold=None, cross_date_override=None):
    """Group ``items`` (dicts with at least ``text``, and ``date`` where known) into candidates.

    Returns ``[{"members": [items...], "similarity": float}, ...]`` for clusters of >= 2, in input
    order. ``similarity`` is the min pairwise char-difflib ratio over DATE-MASKED text.

    TWO passes, because the date is the strongest available discriminator and also fallible:

    1. WITHIN a date. A patient who visits one physiotherapist six times produces six near-identical
       forms that are NOT duplicates, and the date is what separates them - a real 7-member candidate
       spanned 7 distinct dates. Rows whose date is unknown share one bucket and are decided on
       content alone, which is the previous behaviour and the right one for records built by the
       aggregate path, where every row carries "-".
    2. ACROSS dates, admitted only at ``cross_date_override`` on masked text. This pass is NOT
       optional bookkeeping: a genuine re-scan can legitimately carry two different transcribed dates
       (one copy bearing a fax or re-send stamp the other lacks), and a reviewer-confirmed pair scored
       0.998 with exactly that shape. Without this pass the date becomes an absolute veto and that
       pair - and every future one like it - is lost silently.

    Both thresholds default from config so they are tunable by env.
    """
    settings = get_settings()
    if jaccard_threshold is None:
        jaccard_threshold = settings.dupe_jaccard_threshold
    if cross_date_override is None:
        cross_date_override = settings.dupe_similarity_override
    sigs = [_sig(it.get("text")) for it in items]
    dates = [_norm(it.get("date")) for it in items]
    n = len(items)
    # TWO structures over the same rows. `parent` is the cluster itself, joined by either branch.
    # `strong` is joined by the CONTENT branch alone, and is what `content_joined` is read from
    # below. It has to be a second structure rather than a tally taken while unioning, because a
    # tally can only see the unions this pass happened to perform: in a cycle one edge is always
    # redundant, and which one that is follows the row order.
    parent = list(range(n))
    strong = list(range(n))

    for i in range(n):
        for j in range(i + 1, n):
            # Jaccard first for every pair, same date or not: it is a set intersection, and it is the
            # cheap gate that keeps the quadratic difflib below off pairs that share nothing.
            if _jaccard(sigs[i], sigs[j]) < jaccard_threshold:
                continue
            if dates[i] == dates[j]:
                parent[_find(parent, i)] = _find(parent, j)
            elif _min_difflib([items[i].get("text") or "", items[j].get("text") or ""]) >= (
                cross_date_override
            ):
                parent[_find(parent, i)] = _find(parent, j)
                strong[_find(strong, i)] = _find(strong, j)

    groups: dict[int, list[int]] = {}
    for k in range(n):
        groups.setdefault(_find(parent, k), []).append(k)

    clusters = []
    for members_idx in groups.values():
        if len(members_idx) < 2:
            continue
        members = [items[k] for k in members_idx]
        clusters.append(
            {
                "members": members,
                "similarity": _min_difflib([m.get("text") or "" for m in members]),
                # Content-joined when the content edges ALONE hold this cluster together. That is
                # the provenance `duplicate_gate` needs: the override was already satisfied along a
                # path reaching every member, so re-asking it of the transitive closure - whose
                # minimum no chain longer than one hop can reach - rejects genuine re-scans for a
                # test they cannot pass. False the moment a member hangs off a same-date edge only,
                # because that branch joins without scoring content at all. A same-date edge
                # BESIDE a content path revokes nothing: it adds no member the content did not
                # already reach, and connectivity does not depend on which edge came first.
                "content_joined": len({_find(strong, k) for k in members_idx}) == 1,
            }
        )
    return clusters


_UNKNOWN = {"", "-", "n/a", "none", "unknown"}


def _norm(value) -> str:
    """Lowercased, whitespace-collapsed comparison key; "" for the absent-value sentinels."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return "" if text in _UNKNOWN else text


def duplicate_gate(members, similarity, override=None, content_joined=False) -> bool:
    """Whether a candidate cluster is plausible enough to spend a confirm call on.

    Renamed from ``date_title_gate`` because it no longer looks only at date and title: a shared
    CATEGORY now stands in for a shared title. A name that describes the old rule would mislead the
    next reader, which is how the wrong field gets trusted.

    The rule: one date, plus one title OR one category. Two sub-documents that agree on none of that
    are very likely a recurring form series rather than copies - the dominant false positive on real
    records, where a 7-member candidate spanned 7 distinct dates (repeat visits to one provider on a
    shared form).

    Category is an ALTERNATIVE, never a requirement. It is the least reliable of the three - derived
    from the title plus first-page OCR through a cascade with a low-confidence flag - and as an
    alternative it can only ever ADMIT a pair, so a wrong category cannot hide a duplicate. Requiring
    it would convert a visible false cluster into an invisible missed one, and a missed duplicate
    ships two near-identical paragraphs to a client with nothing on screen to hint at it.

    High content similarity still overrides everything, because a genuine re-scan can legitimately
    carry two different transcribed dates. Measured on 22 live clusters: real duplicates scored 0.994
    and above, the worst false positive 0.823, and the reviewer-confirmed 0.998 pair had two dates.

    Runs BEFORE the model call deliberately: it improves precision AND removes a Vertex call per
    rejected cluster, which matters directly under shared-quota pressure. An absent date, title or
    category is UNKNOWN, never a match - two rows that both say "-" have told us nothing. On records
    built by the aggregate path every row carries "-", so the gate correctly falls back to content
    similarity alone there.
    """
    if override is None:
        override = get_settings().dupe_similarity_override
    dates = {_norm(m.get("date")) for m in members}
    titles = {_norm(m.get("title")) for m in members}
    categories = {_norm(m.get("category")) for m in members}
    same_date = len(dates) == 1 and "" not in dates
    same_title = len(titles) == 1 and "" not in titles
    same_category = len(categories) == 1 and "" not in categories
    if same_date and (same_title or same_category):
        return True
    # `content_joined` says the edges that already cleared `override` on their own content reach
    # every member of this cluster. Testing the CLOSURE minimum as well applies the same threshold
    # twice at two different scopes, and the second is unsatisfiable: in a chain A~B~C the A-C pair
    # was never required to be similar and generally is not. Measured on the box at 0.90, that
    # rejected 21 candidates - 20 of them chains of strong edges, none sharing a date - whose
    # STRONGEST pair had a median of 1.000, byte-identical after date masking.
    #
    # Deliberately a separate branch rather than folding into the line below: the caller in
    # tasks.py still tests `similarity` against `dupe_model_override` to decide whether to SKIP the
    # confirm call, and that one must keep reading the minimum. A chain admitted here has to be
    # adjudicated by the model, never accepted wholesale.
    if content_joined:
        return True
    return similarity is not None and similarity >= override


def confirm_cluster(members, model=None):
    """Which of a candidate cluster's ``members`` (dicts with title/date/text) are copies of the SAME
    document. Returns the confirmed sublist (>= 2) or [].

    Fail-safe: on any model/parse error, TRUST the algorithmic candidate (return all members) - a
    broken confirm should surface the group for human review, never silently hide a real duplicate.
    """
    if len(members) < 2:
        return []
    model = model or get_settings().classify_model
    blocks = []
    for i, m in enumerate(members, 1):
        excerpt = (m.get("text") or "")[:_EXCERPT_CHARS]
        blocks.append(
            f"[{i}] title: {m.get('title') or '-'} | date: {m.get('date') or '-'}\n{excerpt}"
        )
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents="\n\n---\n\n".join(blocks),
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=256,
                response_mime_type="application/json",
                response_schema=_CONFIRM_SCHEMA,
                system_instruction=CONFIRM_PROMPT,
            ),
        )
        data = json.loads((response.text or "").strip())
        idxs = sorted(
            {
                i
                for i in data.get("duplicate_indices", [])
                if isinstance(i, int) and 1 <= i <= len(members)
            }
        )
        confirmed = [members[i - 1] for i in idxs]
        return confirmed if len(confirmed) >= 2 else []
    except Exception as exc:
        logger.warning("dedup confirm failed; trusting the algorithmic candidate: %s", exc)
        return list(members)
