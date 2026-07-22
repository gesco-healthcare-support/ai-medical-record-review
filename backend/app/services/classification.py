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
        (
            r"\bmri\b|\bct\b|ct scan|x-? ?ray|\bemg\b|\bncs\b|diagnostic study|laborator"
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
    """Return a category id if a high-precision rule matches the title, else None."""
    text = (title or "").lower()
    for pattern, category in _RULES:
        if pattern.search(text):
            return category
    return None


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
        "list. Choose 100 only if none of the specific categories fit.\n\n"
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
