"""Summary faithfulness verify pass: a second LLM call that rewrites a summary to drop statements
unsupported by, or contradicting, its own OCR source, and reports what it changed (problem #3).

Distinct from services.verify_pass (that verifies SEGMENTATION boundaries). One temp-0 call per
summary. Fail-safe: on any model or parse failure it returns the summary UNCHANGED with no issues -
a broken check must never degrade a good summary.
"""

import json
import logging

from google.genai import types

from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry

logger = logging.getLogger(__name__)

VERIFY_PROMPT = (
    "You audit a summary of a medical-record sub-document for faithfulness to its SOURCE text.\n"
    "- Find every statement in the SUMMARY that is NOT supported by the SOURCE, that CONTRADICTS "
    "the SOURCE, or that contradicts another statement in the summary.\n"
    "- Return a corrected summary that removes or fixes ONLY those statements. Do NOT add new "
    "information, do NOT re-style faithful sentences, and do NOT drop content that IS supported. "
    "Copy dates, percentages, measurements, ratings, and medication names/doses exactly.\n"
    "- If the summary is already fully faithful, return it unchanged with an empty issues list.\n"
    "- Each issue: `type` is 'unsupported' or 'contradiction'; `detail` is a short phrase naming "
    "the offending claim (no PHI beyond what the claim already states)."
)

# google-genai accepts a dict schema alongside response_mime_type=application/json (same dict-schema
# path services.verify_pass uses for its enum). Keeps the model's output parseable without a retry.
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fixed_text": {"type": "STRING"},
        "issues": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": ["unsupported", "contradiction"]},
                    "detail": {"type": "STRING"},
                },
                "required": ["type", "detail"],
            },
        },
    },
    "required": ["fixed_text", "issues"],
}


def verify_summary(model, source_text, summary_text):
    """Audit ``summary_text`` against ``source_text``.

    Returns ``{"fixed_text": str, "issues": list[dict]}``. ``issues`` is non-empty only when the
    model found something to fix. On empty input or ANY failure, returns the original summary with
    no issues (fail-safe).
    """
    if not (summary_text or "").strip():
        return {"fixed_text": summary_text, "issues": []}
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=f"SOURCE:\n{source_text}\n\nSUMMARY:\n{summary_text}",
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                system_instruction=VERIFY_PROMPT,
            ),
        )
        data = json.loads((response.text or "").strip())
        fixed = (data.get("fixed_text") or "").strip()
        issues = data.get("issues") or []
        # A blank fixed_text means the model gave nothing usable - keep the original.
        if not fixed:
            return {"fixed_text": summary_text, "issues": []}
        return {"fixed_text": fixed, "issues": issues}
    except Exception as exc:
        logger.warning("summary verify failed; keeping original: %s", exc)
        return {"fixed_text": summary_text, "issues": []}
