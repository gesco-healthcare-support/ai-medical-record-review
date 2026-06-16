"""B5 categorization cascade: deterministic rules -> local embeddings -> Gemini enum.

Replaces the single-stage difflib fuzzy matcher (``categorization.categorize_documents``).
A sub-document title (and, on low confidence, its first-page OCR text) is classified into a
category id. Conflicting or weak results are flagged for manual review rather than silently
bucketed into the catch-all.

Stages, in order:
  1. Rules     - high-precision keyword/regex on the title; a hit short-circuits (no LLM cost).
  2. Embedding - local sentence-transformers nearest-category (semantic, PHI stays local).
  3. LLM       - Gemini constrained-enum classification (cannot emit an invalid category).
The embedding and LLM votes cross-check each other: agreement -> confident; disagreement ->
flag for review.

torch/sentence-transformers is imported lazily (only when the embedding stage runs), so
importing this module does not pull in torch.
"""

import re
from dataclasses import dataclass

import numpy as np
from google.genai import types

from mrr_ai.extensions import genai_client
from mrr_ai.taxonomy import ALLOWED_IDS, CATEGORIES, DEFAULT_ID

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_LLM_MODEL = "gemini-flash-latest"

# Ordered high-precision rules; first match wins. Specific categories precede the
# categories they could be confused with (e.g. supplemental QME/AME -> 12 before QME/AME -> 13).
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), category)
    for pattern, category in (
        (r"supplement\w*.{0,40}\b(qme|ame|pqme)\b|\b(qme|ame|pqme)\b.{0,40}supplement", "12"),
        (r"\b(qme|ame|pqme)\b|qualified medical evaluator|agreed medical evaluator", "13"),
        # PT/chiro/acupuncture specialty wins over the generic progress/consultation rules below.
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

_CATALOG_TEXT = "\n".join(f"- {c.id}: {c.name} - {c.description}" for c in CATEGORIES.values())


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


# --- embedding stage (lazy: torch is only imported/loaded when this runs) -------------------

_model = None
_category_ids = None
_category_matrix = None


def _encode(texts):
    """Encode texts into L2-normalized vectors using the local sentence-transformers model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_EMBED_MODEL_NAME)
    return np.asarray(_model.encode(list(texts), normalize_embeddings=True))


def _category_vectors():
    """Return (ids, matrix) of encoded category corpora, computed once and cached."""
    global _category_ids, _category_matrix
    if _category_matrix is None:
        _category_ids = list(CATEGORIES.keys())
        _category_matrix = _encode([CATEGORIES[i].corpus for i in _category_ids])
    return _category_ids, _category_matrix


def embed_classify(text):
    """Return (category_id, cosine_score) for the nearest category by embedding."""
    ids, matrix = _category_vectors()
    vec = _encode([text])[0]
    sims = matrix @ vec  # both sides are L2-normalized, so this is cosine similarity
    best = int(np.argmax(sims))
    return ids[best], float(sims[best])


# --- LLM stage ------------------------------------------------------------------------------


def llm_classify(text):
    """Classify via Gemini constrained-enum output; returns a valid id or None on failure."""
    prompt = (
        "Classify the medical-record document below into exactly one category id from this "
        "list. Choose 100 only if none of the specific categories fit.\n\n"
        f"{_CATALOG_TEXT}\n\nDocument:\n{text}\n\nReturn only the category id."
    )
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": list(ALLOWED_IDS)},
        system_instruction=(
            "You classify California workers'-compensation medical-record document types. "
            "Return exactly one category id from the allowed set."
        ),
    )
    try:
        response = genai_client.models.generate_content(
            model=_LLM_MODEL, contents=prompt, config=config
        )
    except Exception as exc:
        print(f"LLM classification failed: {exc}")
        return None
    category = (response.text or "").strip()
    return category if category in CATEGORIES else None


# --- fusion ---------------------------------------------------------------------------------


def classify(title, page_text=None):
    """Classify a sub-document, cross-checking the embedding and LLM votes.

    Rules win outright when they fire (high precision). Otherwise the embedding and LLM must
    agree to be confident; disagreement (or an unavailable LLM) assigns a best guess and sets
    ``needs_review`` so the row is flagged for a human.
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
        # A model-load/encode failure must degrade gracefully, never 500 the route.
        print(f"Embedding classification failed: {exc}")
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
