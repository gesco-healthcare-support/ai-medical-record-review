"""B5 categorization cascade: deterministic rules -> local embeddings -> Gemini enum (ported).

A sub-document title (and, on low confidence, its first-page OCR text) is classified into a
category id. Conflicting or weak results are flagged for manual review rather than silently
bucketed into the catch-all.

torch/sentence-transformers is imported lazily (only when the embedding stage runs), so importing
this module does not pull in torch - the WEB tier never runs classify(); only the segment worker
does. The catalog is read on SHORT-LIVED sessions (get_sessionmaker) so classify() stays
thread-safe under the CLASSIFY_WORKERS pool and needs no Flask app context (the worker's own
per-process engine backs it).
"""

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np
from google.genai import types

from app.config import get_settings
from app.db import get_sessionmaker
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.taxonomy import CATEGORIES, DEFAULT_ID

logger = logging.getLogger(__name__)

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# Administrative paperwork that wraps a record: routing slips, cover letters, emails, declarations,
# proofs of service, records requests, appointment/evaluation notices. These are checked SEPARATELY
# from the document-type rules below (see match_rules), because such a title routinely names the
# document it accompanies - "Cover Letter - PR-2 Progress Report" is a progress report with a cover
# page, while "Email - AME Evaluation Cover Letter" is only correspondence. General (100) is
# unchecked for summarization by default, so claiming the first of those would silently drop a real
# report from the summary.
_ADMIN_RULES: tuple[re.Pattern, ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"routing (sheet|slip|form)|records? routing",
        # `(ame|qme|pqme) letter` added 2026-08-18. A letter APPOINTING or instructing an evaluator
        # is procedural, but only "cover"/"transmittal" were listed, so "Joint AME Letter" matched no
        # administrative rule at all and the bare `ame` in rule 13 answered it. Safe because
        # _EVALUATOR_MENTION withholds ONLY 13: a real document type in the same title still wins, so
        # "Supplemental AME Letter" keeps 12.
        r"\b(cover|transmittal) letter\b|\b(ame|qme|pqme) letter\b"
        r"|\bcorrespondence\b|^\s*e-?mail\s*$",
        # `declaration of service` added UNANCHORED 2026-08-18. `^declaration\b` only matches a title
        # that STARTS with the word, so "QME Declaration of Service" was answered 13 by the evaluator
        # mention while a bare "Declaration of Service" was answered 100 - the same document, decided
        # by word order.
        #
        # Both additions are confirmed against the human deliverable for record 7fb2b543, whose own
        # list of pages NOT remarked upon names "joint AME letter" and "AME or QME declaration of
        # service of medical legal report". See the NOT-fixed note below for the second one.
        r"^declaration\b|\bdeclaration of service\b|proof of service"
        r"|certificate of (service|mailing)|declaration under penalty",
        r"schedule of records|index of records|records? (request|index)"
        r"|request for (medical )?records",
        r"\b(request|notice|scheduling) (for |of |to )?[\w\s-]{0,24}\b(evaluation|examination)\b"
        r"|\b(evaluation|examination) (request|notice|appointment)\b",
    )
)

# The one rule that fires on a MENTION of the evaluator rather than on a document type. A cover
# letter or an appointment notice about an AME says "AME" too, so this category alone does not
# outrank an administrative match - any other rule does.
_EVALUATOR_MENTION = "13"

# Document types that OUTRANK a bare evaluator mention, even with no administrative rule in play.
# Rule 13 sits second in _RULES and first-match-wins, so before this "AME Deposition Transcript"
# answered 13 rather than 9 - a transcript of the AME being QUESTIONED, filed as the AME's own
# report and summarized with the evaluation prompt, which asks for diagnoses, causation and
# apportionment that a transcript does not carry.
#
# Deliberately an explicit SET rather than moving rule 13 down the list. Reordering cannot express
# this: rules 1 and 2 sit ahead of these four, so any position that lets a deposition win also lets
# "progress report" and "permanent and stationary" win - and those legitimately describe the
# evaluator's OWN report ("AME Permanent and Stationary Report" is 13, not 2).
#
# 2026-08-18: decided in-house, NOT confirmed with eData. Reversible - drop an id to restore the
# previous answer for that shape.
_EVALUATOR_YIELDS_TO = frozenset({"3", "8", "9", "14"})  # imaging, operative, deposition, lab

# Words that name a DOCUMENT rather than the paperwork wrapped around it. The segmenter is told to
# fold a cover sheet into the document it travels with and to title the record from the visible
# header (services/gemini.py), so "Cover Letter - AME Report" is one record containing a report.
# When a title names a document like this, the administrative rules stand down and the normal
# cascade decides - including the embedding + LLM stages for report types no rule covers.
# "records" is deliberately absent: "Schedule of Records" and "Cover Letter - Submission of Medical
# Records" are paperwork about records, not records.
_DOCUMENT_NOUN = re.compile(
    r"\b(report|transcript|notes?|study|scan|imaging|x-? ?ray|chart|questionnaire|results?)\b"
)

# The one shape where a document noun names what the paperwork is ABOUT rather than what the pages
# ARE: "...Declaration of Service OF Medical - Legal Report". The noun is the object of the service,
# so _DOCUMENT_NOUN must not stand the administrative rule down for it. This is the case pinned
# xfail in #119 as needing a decision.
#
# Anchored on the trailing "of" and nothing wider, because a real evaluation genuinely DOES travel
# with a service page - "QME Report - Proof of Service" and "Panel QME Report with Declaration of
# Service" are category 13 and pinned as such in the suite. Word order is the whole distinction:
# paperwork-first with the document as its grammatical object, versus document-first with the
# paperwork attached. A broader test (treating every declaration as standalone) was written and
# measured first; it broke both of those pinned titles, which is what narrowed it to this.
#
# 2026-08-18: decided in-house, NOT confirmed with eData.
_PAPERWORK_ABOUT_A_DOCUMENT = re.compile(
    r"\b(declaration|proof|certificate) of (service|mailing) of\b"
)

# Ordered high-precision rules; first match wins. Specific categories precede the categories they
# could be confused with (e.g. supplemental QME/AME -> 12 before QME/AME -> 13).
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), category)
    for pattern, category in (
        (r"supplement\w*.{0,40}\b(qme|ame|pqme)\b|\b(qme|ame|pqme)\b.{0,40}supplement", "12"),
        (r"\b(qme|ame|pqme)\b|qualified medical evaluator|agreed medical evaluator", "13"),
        (r"physical therapy|chiropractic|chiropractor|acupuncture|\bpt\b initial|pt progress", "5"),
        (
            r"\bpr-?4\b|permanent and stationary|\bp ?& ?s\b|maximum medical improvement"
            r"|\bmmi\b|doctor'?s first report|\bdfr\b|initial.{0,20}consultation",
            "2",
        ),
        (r"\bpr-?2\b|progress report|progress note|office visit|follow ?-? ?up", "1"),
        # MODALITY terms only. `laborator` used to be here and it defeated D-01/D-02 entirely: that
        # register item made 3 modality-based and 14 specimen-based, and rewrote both taxonomy
        # descriptions so the embedding and LLM stages agree - but rules run FIRST and short-circuit,
        # so every "Laboratory Results ..." title matched here and never reached the fixed stages.
        # Measured 2026-08-13 on a synthetic record: a comprehensive metabolic panel classified 3,
        # unflagged (a rule hit is always high-confidence), and was summarized with the diagnostic
        # prompt at 2.15x the category-14 human median. Deleting the token is enough - 14's own rule
        # below then matches, and imaging keeps priority for mixed titles like "Radiology Test
        # Results" because this rule still precedes it.
        (
            r"\bmri\b|\bct\b|ct scan|x-? ?ray|\bemg\b|\bncs\b|diagnostic study"
            r"|mammogram|sleep study|colonoscopy|dexa|ultrasound|radiolog",
            "3",
        ),
        (
            r"operative report|surgical patholog|patholog|operation performed|oversight physician",
            "8",
        ),
        (r"deposition", "9"),
        (r"\brfa\b|request for authorization", "10"),
        (
            r"adjudication of claim|application for adjudication|compensation claim|\bdwc-? ?1\b",
            "7",
        ),
        (r"comprehensive interval history|medical decision making", "11"),
        (r"gi outpatient|outpatient procedure h ?& ?p", "4"),
        (r"lab(oratory)? results|test results", "14"),
    )
)


@dataclass
class Classification:
    """Result of classifying one sub-document."""

    category: str
    confidence: str  # "high" | "low"
    method: str
    needs_review: bool


def match_rules(title):
    """Return a category id if a high-precision rule matches the title, else None.

    Document beats wrapper, in two steps. If the title names a document at all (_DOCUMENT_NOUN, e.g.
    "Cover Letter - Psychological Evaluation Report"), the administrative rules stand down entirely
    and the normal cascade answers - falling through to the embedding + LLM stages when no keyword
    rule fits, rather than burying an unrecognised report in General. Otherwise the administrative
    match holds, except where a document-type rule also fired ("Transmittal Letter - MRI Lumbar
    Spine" is an MRI); category 13 does not count there, because it fires on a mere mention of the
    evaluator, which correspondence about an AME contains too.
    """
    text = (title or "").lower()
    matches = [category for pattern, category in _RULES if pattern.search(text)]
    if _EVALUATOR_MENTION in matches and not _EVALUATOR_YIELDS_TO.isdisjoint(matches):
        matches = [c for c in matches if c != _EVALUATOR_MENTION]
    administrative = any(pattern.search(text) for pattern in _ADMIN_RULES)
    carries_a_document = _DOCUMENT_NOUN.search(text) and not _PAPERWORK_ABOUT_A_DOCUMENT.search(
        text
    )
    if not administrative or carries_a_document:
        return matches[0] if matches else None
    return next((c for c in matches if c != _EVALUATOR_MENTION), DEFAULT_ID)


# --- catalog cache (DB-backed, invalidated on edit) -----------------------------------------
# The classifier's category set comes from the editable DB catalog (auto-assignable only). The
# derived state (catalog text for the LLM, ids, embedding matrix) is cached per process and rebuilt
# when the catalog revision changes. reset_catalog_cache() runs at worker startup. Reads fall back
# to the taxonomy constants when the DB is unavailable (a bare unit test), preserving pre-DB
# behavior.
_catalog_lock = threading.Lock()
_catalog_version_seen = None
_catalog_categories = None
_catalog_text_cache = ""
_category_ids = None
_category_matrix = None

_model = None
# SentenceTransformer.encode is not documented as thread-safe and classify() runs on a thread
# pool, so the encode path is serialized. Encoding is milliseconds; the LLM call dominates.
_embed_lock = threading.Lock()


def reset_catalog_cache():
    """Drop the cached catalog + embedding matrix so the next classify reloads from the DB."""
    global _catalog_version_seen, _catalog_categories, _catalog_text_cache
    global _category_ids, _category_matrix
    with _catalog_lock:
        _catalog_version_seen = None
        _catalog_categories = None
        _catalog_text_cache = ""
        _category_ids = None
        _category_matrix = None


def _catalog_version():
    """Current catalog revision, or -1 when the DB is unavailable (constants fallback)."""
    try:
        with get_sessionmaker()() as session:
            return catalog.catalog_version(session)
    except Exception:
        return -1


def _auto_assign_categories():
    """Auto-assignable categories as dicts; taxonomy constants when the DB is unavailable."""
    try:
        with get_sessionmaker()() as session:
            rows = catalog.get_categories(session, auto_assign=True)
        if rows:
            return rows
    except Exception:
        pass
    return [
        {"id": c.id, "name": c.name, "description": c.description, "examples": list(c.examples)}
        for c in CATEGORIES.values()
    ]


def _corpus(category):
    """Representative text for a category dict (mirrors taxonomy.Category.corpus)."""
    examples = category.get("examples") or []
    return f"{category['name']}. {category['description']} Examples: " + "; ".join(examples)


def _refresh_locked():
    """Reload the catalog if its revision changed. Caller must hold ``_catalog_lock``."""
    global _catalog_version_seen, _catalog_categories, _catalog_text_cache
    global _category_ids, _category_matrix
    version = _catalog_version()
    if version != _catalog_version_seen or _catalog_categories is None:
        categories = _auto_assign_categories()
        _catalog_categories = categories
        _catalog_text_cache = "\n".join(
            f"- {c['id']}: {c['name']} - {c['description']}" for c in categories
        )
        _category_ids = None  # force the embedding matrix to rebuild for the new set
        _category_matrix = None
        _catalog_version_seen = version


def _catalog_text():
    with _catalog_lock:
        _refresh_locked()
        return _catalog_text_cache


def _allowed_ids():
    with _catalog_lock:
        _refresh_locked()
        return [c["id"] for c in _catalog_categories]


def _encode(texts):
    """Encode texts into L2-normalized vectors using the local sentence-transformers model."""
    global _model
    with _embed_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(_EMBED_MODEL_NAME)
        return np.asarray(_model.encode(list(texts), normalize_embeddings=True))


def _category_vectors():
    """Return (ids, matrix) of encoded category corpora, rebuilt when the catalog changes."""
    global _category_ids, _category_matrix
    with _catalog_lock:
        _refresh_locked()
        if _category_matrix is None:
            _category_ids = [c["id"] for c in _catalog_categories]
            _category_matrix = _encode([_corpus(c) for c in _catalog_categories])
        return _category_ids, _category_matrix


def embed_classify(text):
    """Return (category_id, cosine_score) for the nearest category by embedding."""
    ids, matrix = _category_vectors()
    vec = _encode([text])[0]
    sims = matrix @ vec  # both sides are L2-normalized, so this is cosine similarity
    best = int(np.argmax(sims))
    return ids[best], float(sims[best])


def llm_classify(text, model=None):
    """Classify via Gemini constrained-enum output; returns a valid id or None on failure.

    Defaults to settings.classify_model (the cheapest tier - this is a short, structured enum task).
    ``model`` is overridable so an A/B can compare tiers on identical inputs.
    """
    allowed = _allowed_ids()
    prompt = (
        "Classify the medical-record document below into exactly one category id from this "
        "list. Choose 100 only if none of the specific categories fit.\n"
        "Administrative and correspondence documents - routing slips, cover letters, emails and "
        "faxes, legal declarations, proofs of service, records requests and record indexes - are "
        "100 even when they mention a QME/AME or another document type, because they accompany "
        "that document rather than being it.\n\n"
        f"{_catalog_text()}\n\nDocument:\n{text}\n\nReturn only the category id."
    )
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": list(allowed)},
        system_instruction=(
            "You classify California workers'-compensation medical-record document types. "
            "Return exactly one category id from the allowed set."
        ),
    )
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model or get_settings().classify_model,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        logger.warning("LLM classification failed: %s", exc)
        return None
    category = (response.text or "").strip()
    return category if category in set(allowed) else None


def classify(title, page_text=None):
    """Classify a sub-document, cross-checking the embedding and LLM votes.

    Rules win outright when they fire. Otherwise the embedding and LLM must agree to be confident;
    disagreement (or an unavailable LLM) assigns a best guess and sets ``needs_review``.
    """
    title = (title or "").strip()
    text = (page_text or title).strip()

    rule_category = match_rules(title)
    if rule_category:
        return Classification(rule_category, "high", "rules", needs_review=False)

    if not text:
        return Classification(DEFAULT_ID, "low", "empty", needs_review=True)

    try:
        embed_category, _score = embed_classify(text)
    except Exception as exc:
        logger.warning("embedding classification failed: %s", exc)
        embed_category = None
    llm_category = llm_classify(text)

    if embed_category is None and llm_category is None:
        return Classification(DEFAULT_ID, "low", "no-signal", needs_review=True)
    if llm_category is None:
        return Classification(embed_category, "low", "embedding-only", needs_review=True)
    if embed_category is None:
        return Classification(llm_category, "low", "llm-only", needs_review=True)
    if llm_category == embed_category:
        return Classification(llm_category, "high", "llm+embedding", needs_review=False)
    return Classification(llm_category, "low", "llm-disagree", needs_review=True)
