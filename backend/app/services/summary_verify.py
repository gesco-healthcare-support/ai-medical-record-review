"""Summary faithfulness verify pass: a second LLM call that rewrites a summary to drop statements
unsupported by, or contradicting, its own OCR source, and reports what it changed (problem #3).

Distinct from services.verify_pass (that verifies SEGMENTATION boundaries). One temp-0 call per
summary. Fail-safe: on any model or parse failure it returns the summary UNCHANGED with no issues -
a broken check must never degrade a good summary.
"""

import json
import logging

from google.genai import types

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry

logger = logging.getLogger(__name__)

VERIFY_PROMPT = (
    "You audit the TITLE and SUMMARY of a medical-record sub-document for faithfulness to its "
    "SOURCE text.\n"
    "- Find every statement in the SUMMARY that is NOT supported by the SOURCE, that CONTRADICTS "
    "the SOURCE, or that contradicts another statement in the summary.\n"
    "- Audit the TITLE the same way, and specifically check its dates, its left/right laterality, "
    "and that it invents no study, body part, author, or facility the SOURCE does not name. A "
    "title is the first thing a reader trusts, so a wrong side or a wrong date there is as "
    "damaging as one in the body.\n"
    "- Return a corrected summary and a corrected title that fix ONLY those problems. Do NOT add "
    "new information, do NOT re-style a faithful sentence or title, and do NOT drop content that "
    "IS supported. Copy dates, percentages, measurements, ratings, and medication names/doses "
    "exactly. Keep the title in the capitalised header form it already uses.\n"
    "- If both are already fully faithful, return them unchanged with an empty issues list.\n"
    "- Each issue: `type` is 'unsupported', 'contradiction', 'date', or 'laterality'; `detail` is "
    "a short phrase naming the offending claim (no PHI beyond what the claim already states)."
)

# google-genai accepts a dict schema alongside response_mime_type=application/json (same dict-schema
# path services.verify_pass uses for its enum). Keeps the model's output parseable without a retry.
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fixed_text": {"type": "STRING"},
        "fixed_title": {"type": "STRING"},
        "issues": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {
                        "type": "STRING",
                        # date + laterality added with title auditing: a wrong date or a
                        # left/right flip is neither "unsupported" nor a self-contradiction, so
                        # the old pair could not name what was actually wrong.
                        "enum": ["unsupported", "contradiction", "date", "laterality"],
                    },
                    "detail": {"type": "STRING"},
                },
                "required": ["type", "detail"],
            },
        },
    },
    "required": ["fixed_text", "issues"],
}


def verify_summary(model, source_text, summary_text, title=None):
    """Audit ``summary_text`` and (when given) ``title`` against ``source_text``.

    Returns ``{"fixed_text": str, "fixed_title": str, "issues": list[dict]}``. ``issues`` is
    non-empty only when the model found something to fix. On empty input or ANY failure, returns the
    originals with no issues (fail-safe).

    Title and body share ONE call: the model can then compare them against each other (a title
    naming a study the body never mentions is exactly the kind of drift worth catching), and a
    summary costs one verification call rather than two.
    """
    if not (summary_text or "").strip():
        return {"fixed_text": summary_text, "fixed_title": title, "issues": []}
    prompt = f"SOURCE:\n{source_text}\n\n"
    if title:
        prompt += f"TITLE:\n{title}\n\n"
    prompt += f"SUMMARY:\n{summary_text}"
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                # The reply must hold a corrected copy of the whole summary AND (on a thinking
                # model) the reasoning tokens, which are billed against this same budget. At 4096 a
                # long category-1 or diagnostic summary came back as truncated JSON, which the parse
                # then discarded - silently keeping the unverified original. Track the summary
                # budget, since the output is at minimum as long as the input.
                max_output_tokens=get_settings().summary_max_output_tokens,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                system_instruction=VERIFY_PROMPT,
                # This pass runs on summary_model, which is a THINKING model (2.5-pro). Without an
                # explicit budget the retry seam applies its default of 0, which that model rejects
                # with 400 INVALID_ARGUMENT - and because this function is fail-safe, the rejection
                # was silent: every verify call returned the unchanged summary. Same dynamic budget
                # the summary call uses.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=get_settings().summary_thinking_budget
                ),
            ),
        )
        data = json.loads((response.text or "").strip())
        fixed = (data.get("fixed_text") or "").strip()
        issues = data.get("issues") or []
        # A blank fixed_text means the model gave nothing usable - keep the original.
        if not fixed:
            return {"fixed_text": summary_text, "fixed_title": title, "issues": []}
        # A blank fixed_title falls back to the original: the schema does not require the field, and
        # a title is never replaced by nothing.
        fixed_title = (data.get("fixed_title") or "").strip() or title
        return {"fixed_text": fixed, "fixed_title": fixed_title, "issues": issues}
    except Exception as exc:
        logger.warning("summary verify failed; keeping original: %s", exc)
        return {"fixed_text": summary_text, "fixed_title": title, "issues": []}
