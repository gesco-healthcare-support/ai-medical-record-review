"""Per-document summarization for the review flow - Gemini on the Vertex/BAA path (ported).

Reproduces the legacy per-row behavior (same category prompts, title extraction, decorations).
Callers pass rows + the resolved prompt explicitly, so this service stays DB-free; when the
prompt is omitted it falls back to the hardcoded prompts.py dict (category_11 has none -> the
general prompt, avoiding the historical KeyError).
"""

from google.genai import types

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts
from app.services.summary_doi import extract_injury_date
from app.services.summary_verify import verify_summary

TITLE_PROMPT = (
    "You are an intelligent assistant tasked with extracting the **title** of the document "
    "and the **entity responsible for the encounter**. Follow these instructions:\n\n"
    "1. **Title Extraction**: extract the title if explicitly clear, else infer it from "
    'context (e.g. "PT Progress Note", "Office Visit", "Hospital Discharge"); it can be at '
    'the top or towards the end of the document. If it cannot be inferred, respond `" unknown"`.\n'
    "2. **Name of Entity Responsible for the Encounter**: the person or entity that directly "
    "conducted the encounter (prefer the signature section); never the referring provider. "
    'If unavailable, return `"Unknown"`.\n'
    "3. **Output Format**: a single line `[Title] - [Name of Responsible for Encounter]`. "
    "Never use commas; separate with dashes.\n"
    "4. **Do Not Add Commentary**: return only the extracted information."
)

# Prepended to every category prompt. Extractive-faithfulness rules (each states its WHY so it
# survives edits). An eval on real sub-docs showed this eliminated a 12-fabrication case without
# worsening fabrication; residual contradictions are handled by the verify pass.
HARDENING_PREAMBLE = (
    "CRITICAL FACTUALITY RULES (a medical-legal report depends on these):\n"
    "- Use ONLY information explicitly stated in the text below. Do NOT infer, assume, "
    "extrapolate, or add anything not written - inference is how errors enter the record.\n"
    "- If a detail is absent, OMIT it. Never guess or fill a gap, and never write a point then "
    "say 'not specified'.\n"
    "- Copy dates, percentages, measurements, ratings, and medication names/doses EXACTLY as "
    "written; do not round, convert, or paraphrase a number.\n"
    "- Do NOT contradict yourself: every statement must be consistent with the source and with "
    "your other statements.\n"
    "- If the text is illegible, ambiguous, or internally contradictory, omit that point rather "
    "than resolving it by guessing.\n\n"
)


def _generate(model, system_msg, user_text, temperature):
    response = generate_with_retry(
        get_genai_client(),
        model=model,
        contents=user_text,
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=2048,
            system_instruction=system_msg,
        ),
    )
    return (response.text or "").strip()


def summarize_row(pdf_path, row, model=None, prompt=None, verify=None, extract_doi=None):
    """Summarize one sub-document row -> the legacy output_dict shape.

    row: {start, end, category, date, injury_date, flag}. ``prompt`` is the category's summary
    system prompt (blueprints resolve it DB-first via catalog.get_prompt and inject it); when
    omitted it falls back to the hardcoded prompts.py dict. ``verify`` runs the faithfulness verify
    pass (defaults to settings.summary_verify); callers pass False to skip it (e.g. bundle export).
    ``extract_doi`` (defaults to settings.summary_doi_extract) reads the DOI per-document in
    isolation instead of trusting the propagated row value; False keeps the legacy row value.
    """
    settings = get_settings()
    model = model or settings.summary_model
    if verify is None:
        verify = settings.summary_verify
    if extract_doi is None:
        extract_doi = settings.summary_doi_extract
    if prompt is None:
        key = f"category_{int(row['category']):02d}" if row["category"] != "100" else "category_100"
        prompt = prompts.get(key, prompts["category_100"])
    # Prepend the factuality-hardening rules to the category prompt (applies to DB-resolved and
    # fallback prompts alike, and to any future category).
    system_msg = HARDENING_PREAMBLE + prompt

    pages = list(range(int(row["start"]), int(row["end"]) + 1))
    text = extract_text_from_selected_pages(pdf_path, pages)
    if not text.strip():
        # Fail fast with a clear reason: sending empty text to Gemini yields a cryptic
        # "Model input cannot be empty" 400. Blank/image-only pages hit this.
        raise EmptyExtractionError(f"no OCR text for pages {row['start']}-{row['end']}")

    # Summary body runs at settings.summary_temperature (default 0.0 for determinism); the title
    # is pure extraction, always 0.
    summary = _generate(model, system_msg, text, temperature=settings.summary_temperature)
    title = _generate(model, TITLE_PROMPT, text, temperature=0.0)

    # DOI only when THIS document states it: an isolated per-document vision call (no neighbours to
    # copy from) supersedes the segmentation-propagated injury_date. extract_doi=False keeps the
    # legacy row value. extract_injury_date is fail-safe (returns "-" -> no prefix).
    injury = (
        extract_injury_date(pdf_path, row["start"], row["end"], model)
        if extract_doi
        else row["injury_date"]
    )
    doi_final = "" if injury in ("", "-") else f"**DOI**:{injury},"
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""

    # Faithfulness verify pass (problem #3): audit the body against its source and, ONLY when the
    # pass flags issues, keep the corrected body as verifiedText (the raw summaryText stays as the
    # immutable model output). No issues -> verifiedText/verifyIssues stay None, so the summary is
    # unchanged and unflagged. verify_summary is fail-safe (returns the original on any error).
    verified_text = None
    verify_issues = None
    if verify:
        result = verify_summary(model, text, summary)
        if result["issues"]:
            verified_text = f"{doi_final} {result['fixed_text']}"
            verify_issues = result["issues"]

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        "summaryText": f"{doi_final} {summary}",
        "verified": bool(verify),
        "verifiedText": verified_text,
        "verifyIssues": verify_issues,
        # The exact model input, so callers can persist the fine-tuning pair.
        "sourceText": text,
    }
