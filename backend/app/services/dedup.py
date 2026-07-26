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


def _sig(text):
    return set(re.findall(r"[a-z0-9]{2,}", (text or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _min_difflib(texts):
    """The lowest pairwise char-level ratio across ``texts`` (1.0 for a single item). Low = the
    members diverge in content (likely a recurring-form series), high = near-identical re-scans."""
    ratios = [
        difflib.SequenceMatcher(None, a, b).ratio() for a, b in itertools.combinations(texts, 2)
    ]
    return round(min(ratios), 3) if ratios else 1.0


def cluster_rows(items, jaccard_threshold=0.7):
    """Group ``items`` (dicts with at least ``text``) by word-set Jaccard >= threshold via union-find.

    Returns a list of candidate clusters, each ``{"members": [items...], "similarity": float}``, only
    for clusters with >= 2 members, in input order. ``similarity`` is the min pairwise char-difflib
    ratio - a cheap true-dupe vs shared-template signal the UI shows and the confirm step refines.
    """
    sigs = [_sig(it.get("text")) for it in items]
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if _jaccard(sigs[i], sigs[j]) >= jaccard_threshold:
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
