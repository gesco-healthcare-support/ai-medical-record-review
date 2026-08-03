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

# The house rules the audit enforces IN ADDITION to faithfulness. Kept here rather than imported
# from summarize_engine because that module imports this one; the generation-side wording lives in
# HARDENING_PREAMBLE and the two must be edited together. They are deliberately phrased differently:
# generation says "do not write X", the audit says "find and remove X", which is what makes a second
# pass worth paying for at all.
#
# Why this exists: every one of these violations is FAITHFUL to the source, so the faithfulness audit
# was structurally forbidden from touching them ("do NOT re-style a faithful sentence"). A live
# 2026-07-30 export showed the generation-side rules for pain descriptors and capitalisation being
# ignored even though both had been deployed for a day. A rule the model skips at generation needs a
# second reader, not louder wording.
_HOUSE_RULES = (
    "HOUSE RULES (a violation here is a defect even though the SOURCE supports it):\n"
    "1. HEIGHT AND WEIGHT: remove the patient's height and weight. Those two ONLY - leave every "
    "other vital sign (blood pressure, pulse, respiration, temperature, oxygen saturation) exactly "
    "as the summary has it, and never remove a BMI the SOURCE states as a diagnosis.\n"
    "2. PAIN: keep frequency, the numeric rating, and the location. Remove quality words (sharp, "
    "dull, aching, stabbing, throbbing, burning, cramping, shooting) and never let intensity be "
    'stated twice ("moderate 6/10").\n'
    "3. CAPITALISATION: no word, sentence, line, or point in the SUMMARY body may be in capital "
    "letters, even where the SOURCE capitalises it. The repair is always RE-CASING, never deleting "
    "or rewording the text: a company or facility name in title case, an occupation in sentence "
    "case. Genuine acronyms stay: MRI, CT, EMG, NCS, ECG, QME, AME, "
    "PR-2, PR-4, RFA, ADL, TTD, WPI, MMI, HPI, PE, ROM, ICD, CPT. The TITLE is exempt - it is an "
    "all-capitals header by design. A bold point heading such as **Work Status**: is REQUIRED "
    "structure, NOT a stray capitalised header: where one is misnamed, mis-cased, or not a point "
    "this category asks for, RENAME or RE-CASE it and KEEP IT BOLDED. Deleting a heading, removing "
    "its bold, or folding its content into running prose is never a permitted repair.\n"
    "4. RANGE OF MOTION: a measurement must carry whether it is reduced, normal, or increased. Do "
    "not remove or alter the measured number; add the direction if it is missing, taking it from the "
    "SOURCE's own wording or from the normal value the SOURCE prints beside it where either exists.\n"
    "5. DUPLICATION: if the summary reports Findings and Impression (or Conclusion) saying the same "
    "thing, keep the Impression and drop the Findings. Keep both only where Findings states "
    "something the Impression does not.\n"
    "6. PREVIOUS VISITS: content the SOURCE attributes to an EARLIER date than this document's own "
    "date is a recap of a prior encounter and does not belong in this summary. The mechanism of "
    "injury and the injury history may stay, stated once. This rule applies ONLY when a document "
    "date is given below.\n"
)

VERIFY_PROMPT = (
    "You audit the TITLE and SUMMARY of a medical-record sub-document on two counts: faithfulness "
    "to its SOURCE text, and compliance with the house rules below.\n"
    "- YOU FACT-CHECK AND CORRECT. You do not blindly remove or add. Where something is wrong, the "
    "repair is an edit IN PLACE. Deleting content is permitted in exactly two cases: the content is "
    "unsupported by or contradicts the SOURCE, or a house rule below explicitly directs its removal "
    "(rules 1, 2, 5 and 6). Rules 3 and 4 are CORRECTION-ONLY: fix what is wrong about the text and "
    "leave the text itself standing.\n"
    "- Find every statement in the SUMMARY that is NOT supported by the SOURCE, that CONTRADICTS "
    "the SOURCE, or that contradicts another statement in the summary.\n"
    "- Audit the TITLE the same way, and specifically check its dates, its left/right laterality, "
    "and that it invents no study, body part, author, or facility the SOURCE does not name. A "
    "title is the first thing a reader trusts, so a wrong side or a wrong date there is as "
    "damaging as one in the body.\n"
    "- Then apply the HOUSE RULES. These are the one reason you may edit a sentence that is "
    "perfectly faithful.\n"
    "- Return a corrected summary and a corrected title that fix ONLY those problems: the "
    "faithfulness defects and the house-rule violations, nothing else. Do NOT add new information, "
    "do NOT re-style a sentence that breaks neither, and do NOT drop content that IS supported and "
    "breaks no house rule. Copy dates, percentages, measurements, ratings, and medication "
    "names/doses exactly. Keep the title in the capitalised header form it already uses.\n"
    "- If both are already faithful and compliant, return them unchanged with an empty issues "
    "list.\n"
    "- Each issue: `type` is one of 'unsupported', 'contradiction', 'date', 'laterality', 'vitals', "
    "'pain_descriptor', 'capitalization', 'range_of_motion', 'duplicate_finding', 'prior_visit'; "
    "`detail` is a short phrase naming the offending claim (no PHI beyond what the claim already "
    "states).\n\n" + _HOUSE_RULES
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
                        # the old pair could not name what was actually wrong. The six house-rule
                        # types are named separately so the stored issues say WHICH rule fired -
                        # that is the only way to measure whether a rule is working.
                        "enum": [
                            "unsupported",
                            "contradiction",
                            "date",
                            "laterality",
                            "vitals",
                            "pain_descriptor",
                            "capitalization",
                            "range_of_motion",
                            "duplicate_finding",
                            "prior_visit",
                        ],
                    },
                    "detail": {"type": "STRING"},
                },
                "required": ["type", "detail"],
            },
        },
    },
    "required": ["fixed_text", "issues"],
}


def verify_summary(model, source_text, summary_text, title=None, document_date=None):
    """Audit ``summary_text`` and (when given) ``title`` against ``source_text`` and the house rules.

    Returns ``{"fixed_text": str, "fixed_title": str, "issues": list[dict]}``. ``issues`` is
    non-empty only when the model found something to fix. On empty input or ANY failure, returns the
    originals with no issues (fail-safe).

    Title and body share ONE call: the model can then compare them against each other (a title
    naming a study the body never mentions is exactly the kind of drift worth catching), and a
    summary costs one verification call rather than two.

    ``document_date`` is this sub-document's own date. It is what makes house rule 6 checkable: a
    complaint or finding the source attributes to an earlier date is a recap of a visit that has its
    own document elsewhere in the record. Omit it and the rule is skipped rather than guessed at.
    """
    if not (summary_text or "").strip():
        return {"fixed_text": summary_text, "fixed_title": title, "issues": []}
    prompt = f"SOURCE:\n{source_text}\n\n"
    date = str(document_date or "").strip()
    if date and date != "-":
        prompt += f"THIS DOCUMENT'S DATE:\n{date}\n\n"
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
