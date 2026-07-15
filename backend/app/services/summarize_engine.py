"""Per-document summarization for the review flow - Gemini on the Vertex/BAA path (ported).

Reproduces the legacy per-row behavior (same category prompts, title extraction, decorations).
Callers pass rows + the resolved prompt explicitly, so this service stays DB-free; when the
prompt is omitted it falls back to the hardcoded prompts.py dict (category_11 has none -> the
general prompt, avoiding the historical KeyError).
"""

from google.genai import types

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts

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


def summarize_row(pdf_path, row, model=None, prompt=None):
    """Summarize one sub-document row -> the legacy output_dict shape.

    row: {start, end, category, date, injury_date, flag}. ``prompt`` is the category's summary
    system prompt (blueprints resolve it DB-first via catalog.get_prompt and inject it); when
    omitted it falls back to the hardcoded prompts.py dict.
    """
    model = model or get_settings().summary_model
    if prompt is None:
        key = f"category_{int(row['category']):02d}" if row["category"] != "100" else "category_100"
        prompt = prompts.get(key, prompts["category_100"])
    system_msg = prompt

    pages = list(range(int(row["start"]), int(row["end"]) + 1))
    text = extract_text_from_selected_pages(pdf_path, pages)

    # Legacy used temperature 0.8 for the summary body; the title is extraction, so 0.
    summary = _generate(model, system_msg, text, temperature=0.8)
    title = _generate(model, TITLE_PROMPT, text, temperature=0.0)

    doi_final = "" if row["injury_date"] in ("", "-") else f"**DOI**:{row['injury_date']},"
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        "summaryText": f"{doi_final} {summary}",
        # The exact model input, so callers can persist the fine-tuning pair.
        "sourceText": text,
    }
