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


def _min_difflib(texts):
    """The lowest pairwise char-level ratio across ``texts`` (1.0 for a single item). Low = the
    members diverge in content (likely a recurring-form series), high = near-identical re-scans.

    Compares only the first ``_EXCERPT_CHARS`` of each member - the same window the AI-confirm call
    reads. difflib is quadratic in length AND in member count, so full text costs minutes on a
    re-scanned transcript. Measured on a real 6-member cluster (13k chars each): 0.219 truncated vs
    0.068 full, 33ms vs 1132ms - the verdict (a form series, nowhere near a true copy's 1.000) is
    unchanged at 34x less cost.
    """
    excerpts = [mask_dates(text)[:_EXCERPT_CHARS] for text in texts]
    ratios = [
        difflib.SequenceMatcher(None, a, b).ratio() for a, b in itertools.combinations(excerpts, 2)
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
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            # Jaccard first for every pair, same date or not: it is a set intersection, and it is the
            # cheap gate that keeps the quadratic difflib below off pairs that share nothing.
            if _jaccard(sigs[i], sigs[j]) < jaccard_threshold:
                continue
            if dates[i] == dates[j]:
                parent[find(i)] = find(j)
            elif _min_difflib([items[i].get("text") or "", items[j].get("text") or ""]) >= (
                cross_date_override
            ):
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(k)

    clusters = []
    for members_idx in groups.values():
        if len(members_idx) < 2:
            continue
        members = [items[k] for k in members_idx]
        clusters.append(
            {"members": members, "similarity": _min_difflib([m.get("text") or "" for m in members])}
        )
    return clusters


_UNKNOWN = {"", "-", "n/a", "none", "unknown"}


def _norm(value) -> str:
    """Lowercased, whitespace-collapsed comparison key; "" for the absent-value sentinels."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return "" if text in _UNKNOWN else text


def duplicate_gate(members, similarity, override=None) -> bool:
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
