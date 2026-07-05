"""Per-document OpenAI summarization, extracted for the review flow.

Faithfully reproduces the /summarize per-row behavior (same prompts, same title
extraction, same decorations) so the review UI's row-wise summaries and the legacy
Word export stay identical in content. Two deliberate hardenings:
- prompts.get(...) falls back to the general prompt: category_11 has NO prompt in
  prompts.py (latent KeyError crash in the legacy path, found 2026-06-16).
- callers pass rows explicitly; no file/global reads here (services stay Flask-free).
"""

from mrr_ai.extensions import client
from mrr_ai.prompts import prompts
from mrr_ai.services.ocr import extract_text_from_selected_pages

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


def _chat(model, system_msg, user_text):
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": [{"type": "text", "text": f"{system_msg}"}]},
            {"role": "user", "content": [{"type": "text", "text": f"{user_text}"}]},
        ],
        temperature=0.8,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        response_format={"type": "text"},
    )
    return completion.choices[0].message.content


def summarize_row(pdf_path, row, model):
    """Summarize one sub-document row -> the legacy output_dict shape.

    row: {start, end, category, date, injury_date, flag}. The dict feeds both the UI
    (row-wise display) and state.all_data (legacy Word export).
    """
    key = f"category_{int(row['category']):02d}" if row["category"] != "100" else "category_100"
    system_msg = prompts.get(key, prompts["category_100"])

    pages = list(range(int(row["start"]), int(row["end"]) + 1))
    text = extract_text_from_selected_pages(pdf_path, pages)

    summary = _chat(model, system_msg, text)
    title = _chat(model, TITLE_PROMPT, text)

    doi_final = "" if row["injury_date"] in ("", "-") else f"**DOI**:{row['injury_date']},"
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        "summaryText": f"{doi_final} {summary}",
    }
