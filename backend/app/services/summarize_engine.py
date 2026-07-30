"""Per-document summarization for the review flow - Gemini on the Vertex/BAA path (ported).

Reproduces the legacy per-row behavior (same category prompts, title extraction, decorations).
Callers pass rows + the resolved prompt explicitly, so this service stays DB-free; when the
prompt is omitted it falls back to the hardcoded prompts.py dict (category_11 has none -> the
general prompt, avoiding the historical KeyError).
"""

import io
import logging

from google.genai import types
from pdf2image import convert_from_path

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts
from app.services.summary_doi import extract_injury_date
from app.services.summary_verify import verify_summary

logger = logging.getLogger(__name__)

# The header line a human reviewer writes, measured across 812 of 813 entries in ten completed MRR
# deliverables: ALL CAPS, "AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.". The old prompt inverted
# that order, had no facility slot (so the model put letterhead in the title instead), mandated
# dashes, and banned the comma the credential form needs. Diagnostics get an explicit naming form
# because ~18 of 36 measured category-3 titles named the document class ("Radiology Report") rather
# than the study. The date is NOT here: it is already its own column, as in the human layout.
TITLE_PROMPT = (
    "You extract the header line for ONE medical sub-document. Return exactly one line, in this "
    "order, with the elements separated by a period and a space, and the WHOLE line in capital "
    "letters:\n\n"
    "AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.\n\n"
    "1. AUTHOR - the person who conducted and signed THIS encounter, read from the signature "
    "block where there is one. Never the referring provider. Write the name, then a comma, "
    'then the credentials with periods, for example "JANE SMITH, M.D." Non-physicians are '
    "included: M.D., D.O., D.C., P.T., R.N., P.A., N.P., PSY.D., L.V.N., O.D., D.D.S.\n"
    "2. FACILITY - the clinic, imaging centre, hospital, laboratory, or practice that produced "
    "the document, usually on the letterhead.\n"
    "3. DOCUMENT TYPE - what the document IS.\n"
    "   For a diagnostic study, name the study, never its class. Use the form "
    "<MODALITY> OF THE <SIDE IF STATED> <BODY PART> <CONTRAST STATUS IF STATED>, for example "
    '"MRI OF THE CERVICAL SPINE WITHOUT CONTRAST", "CT OF THE HEAD WITHOUT CONTRAST", '
    '"X-RAY OF THE LEFT WRIST", "EMG/NCS OF THE UPPER EXTREMITIES". Never "RADIOLOGY REPORT", '
    '"GENERAL RADIOLOGY PROCEDURE", or "DIAGNOSTIC REPORT". Ultrasound and mammogram studies '
    'are named organ-first, for example "THYROID ULTRASOUND".\n'
    "   A facility name is NOT a document type. If the top of the page carries only letterhead, "
    "read on for the study or report heading, which often sits above the findings.\n\n"
    "If an element is not stated in the document, omit that element and its separator. Do not "
    'write "UNKNOWN", "UNSPECIFIED", or any placeholder - an absent element is left out, the same '
    "way an absent point is left out of the summary body.\n\n"
    "Never include page numbers or page ranges, dates, or the patient's name. Return only the "
    "line, with no commentary."
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
    # Content scope lives here, once, so a category prompt only has to name its own points.
    # Measured against 55 eData deliverables (2115 entries): the length gap is per-category, not
    # uniform - labs 25x, diagnostic studies 4.3x, therapy notes 3.3x, treating reports 2.4x, while
    # medico-legal evaluations and depositions run SHORTER than the human convention. The old note
    # here cited a corpus-wide median of 240 chars; that figure is a mix artifact (a quarter of the
    # corpus is labs, forms and one-line impressions) and must not be used as a target.
    #
    # The employer/occupation carve-out was removed on measured evidence: the human corpus confines
    # both to WCAB filings (51%/39%) and comprehensive evaluations (40%/25%), and uses them in 1%
    # of treating notes and 0% of imaging, therapy and lab entries. A blanket carve-out added them
    # to ~1600 entries where the convention omits them. Categories 2 and 7 name them directly.
    "CONTENT RULES (what belongs in the summary):\n"
    "- Include a point ONLY if the category rules below name it for this document type. Do not "
    "add a point the rules do not list, however relevant it looks - unrequested detail is the "
    "main reason summaries run long.\n"
    "- Report positive and abnormal findings only when describing an examination, a history, or a "
    "clinical assessment. Omit anything recorded as normal, negative, unremarkable, or within "
    "normal limits; a reader assumes anything not mentioned was normal.\n"
    "- That rule does NOT apply to the conclusion of a diagnostic study or to a laboratory or test "
    "result. Report the impression, result, or verdict exactly as stated even when it is normal or "
    "negative - for those documents the verdict IS the content, and omitting it leaves the summary "
    "empty.\n"
    "- If the document contains a review of earlier medical records inside it, record that the "
    "review is present and take from it only the diagnostic studies it reports. Never summarize "
    "the embedded review in whole - it restates records that are summarized in their own right "
    "elsewhere in the set.\n"
    "- Do NOT write ICD, CPT, or other billing codes, even when the document lists them.\n"
    "- For pain, give frequency, intensity on the scale the document uses, and location, and "
    "nothing else. Do not add qualitative descriptors, and never state intensity twice - write "
    '"6/10", not "moderate 6/10".\n\n'
    "FORMATTING (STRICT - overrides any layout instruction in the category rules below):\n"
    "- Write the ENTIRE summary as ONE continuous paragraph. Do NOT use line breaks, blank lines, "
    "bullet points, or numbered lists to separate points; when the rules below organize the content "
    "into named points or sections, run those points together inline in one single paragraph.\n"
    # Depositions are the one measured exception: the human convention is one line per transcript
    # page (median gap between referenced pages is 1, across 978 transitions), so the single-
    # paragraph rule would destroy the format rather than tidy it.
    "- The single-paragraph rule does NOT apply to deposition or recorded-statement transcripts. "
    "Summarize those page by page, one line per page, each beginning with the page and line "
    "reference, and do not merge them into a paragraph.\n"
    "- Bold ONLY the short point/section labels, e.g. **Subjective Complaints**, **Diagnoses**, "
    "**Work Status**. Do NOT bold the text that follows a label, and NEVER bold a whole sentence, a "
    "whole point, or the entire summary - bolding everything makes the emphasis meaningless.\n\n"
)


_MULTIMODAL_INSTRUCTION = (
    "The images above are the scanned page(s) of this sub-document; the OCR text of the same pages "
    "follows. Use BOTH - treat the images as authoritative wherever the OCR is garbled, missing, or "
    "from a table, checkbox, or handwriting - and summarize per the system instructions.\n\n"
    "OCR TEXT:\n"
)


def _hit_token_cap(response) -> bool:
    """True when the model stopped because it exhausted max_output_tokens, i.e. the reply is cut
    off. Read defensively (name or str) so a client-library enum change cannot crash a summary."""
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        if str(getattr(reason, "name", reason)).upper().endswith("MAX_TOKENS"):
            return True
    return False


def _generate(model, system_msg, contents, temperature, max_output_tokens=None):
    """One Gemini call -> ``(text, truncated)``. ``contents`` is the OCR text, or (multimodal) a list
    of page-image Parts followed by the OCR text. ``truncated`` is True when the reply hit the token
    budget, which callers surface instead of storing a half summary as finished."""
    settings = get_settings()
    if max_output_tokens is None:
        max_output_tokens = settings.summary_max_output_tokens
    response = generate_with_retry(
        get_genai_client(),
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system_msg,
            # summary_model is 2.5-pro, a thinking model that rejects the seam's default budget 0;
            # set dynamic thinking here so the seam leaves it (budget 0 only suits the flash tiers).
            thinking_config=types.ThinkingConfig(thinking_budget=settings.summary_thinking_budget),
        ),
    )
    return (response.text or "").strip(), _hit_token_cap(response)


def _page_image_parts(pdf_path, start, end):
    """Rasterize a sub-document's pages to lean JPEG image Parts for multimodal summarization.

    Capped at settings.summary_image_max_pages so a long sub-document cannot blow the payload; the
    full OCR text still covers every page. Rasterized one page at a time to cap peak memory.
    """
    settings = get_settings()
    last = min(int(end), int(start) + settings.summary_image_max_pages - 1)
    parts = []
    for page in range(int(start), last + 1):
        for image in convert_from_path(
            pdf_path, first_page=page, last_page=page, dpi=settings.summary_image_dpi
        ):
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
            parts.append(types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg"))
    return parts


def summarize_row(pdf_path, row, model=None, prompt=None, verify=None, extract_doi=None):
    """Summarize one sub-document row -> the legacy output_dict shape.

    row: {start, end, category, date, injury_date, flag} and OPTIONALLY ``source_text`` - the OCR
    text of exactly those pages, which the duplicate check already extracted and stored. When it is
    present and non-blank this reuses it instead of OCRing the same pages a second time; blank or
    absent means OCR here as before, so a page whose OCR failed still gets another chance.
    ``_store_rows`` carries ``source_text`` across edits keyed by (start, end), so reused text
    always belongs to the row's current page range.

    ``prompt`` is the category's summary system prompt (blueprints resolve it DB-first via
    catalog.get_prompt and inject it); when omitted it falls back to the hardcoded prompts.py dict.
    ``verify`` runs the faithfulness verify pass (defaults to settings.summary_verify); callers pass
    False to skip it (e.g. bundle export). ``extract_doi`` (defaults to settings.summary_doi_extract)
    reads the DOI per-document in isolation instead of trusting the propagated row value; False keeps
    the legacy row value.
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

    # Reuse the duplicate check's OCR when it exists: it ran the SAME extraction over the SAME pages
    # and persisted it per row, so a second full pass is pure waste - on a 1500-page record that is
    # ~45 minutes of OCR done twice. Blank text is not reused, so a page whose OCR failed the first
    # time is retried here rather than being permanently condemned to EmptyExtractionError.
    text = (row.get("source_text") or "").strip()
    if not text:
        pages = list(range(int(row["start"]), int(row["end"]) + 1))
        text = extract_text_from_selected_pages(pdf_path, pages)
    if not text.strip():
        # Fail fast with a clear reason: sending empty text to Gemini yields a cryptic
        # "Model input cannot be empty" 400. Blank/image-only pages hit this.
        raise EmptyExtractionError(f"no OCR text for pages {row['start']}-{row['end']}")

    # Summary body runs at settings.summary_temperature (default 0.0 for determinism); the title is
    # pure extraction, always 0. When multimodal is on, the body also gets the page images (OCR text
    # alone garbles tables/handwriting); a rasterize failure degrades to OCR-only rather than failing
    # the row. The title stays OCR-text-only (cheaper and adequate).
    body_contents = text
    if settings.summary_multimodal:
        try:
            body_contents = _page_image_parts(pdf_path, row["start"], row["end"]) + [
                _MULTIMODAL_INSTRUCTION + text
            ]
        except Exception as exc:  # noqa: BLE001 - degrade to OCR-only; never fail a summary on this
            logger.warning(
                "multimodal rasterize failed for pages %s-%s; using OCR-only: %s",
                row["start"],
                row["end"],
                exc,
            )
    summary, truncated = _generate(
        model,
        system_msg,
        body_contents,
        temperature=settings.summary_temperature,
        max_output_tokens=settings.summary_max_output_tokens,
    )
    title, _ = _generate(model, TITLE_PROMPT, text, temperature=0.0)

    # DOI only when THIS document states it: an isolated per-document vision call (no neighbours to
    # copy from) supersedes the segmentation-propagated injury_date. extract_doi=False keeps the
    # legacy row value. extract_injury_date is fail-safe (returns "-" -> no prefix).
    #
    # Deliberately NOT passed `model`: that handed it summary_model (2.5-pro), whose quota is the
    # binding constraint - one evening measured 498 accepted vs 181 rejected 2.5-pro calls, enough to
    # pause a summarize job. Reading a date off a page is extraction, not prose, so it belongs on
    # genai_model (flash), which is what the function defaults to.
    injury = (
        extract_injury_date(pdf_path, row["start"], row["end"])
        if extract_doi
        else row["injury_date"]
    )
    # House grammar (see summary_doi): "**DOI**: <value>." - colon-space, period terminator. Stored
    # summaries written before 2026-07-29 carry the old "**DOI**:<value>," form and stay readable;
    # summary_doi.doi_prefix parses both.
    doi_final = "" if injury in ("", "-") else f"**DOI**: {injury}."
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""

    # Faithfulness verify pass (problem #3): audit the title AND the body against their source and,
    # ONLY when the pass flags issues, keep the corrected pair as verifiedTitle/verifiedText (the raw
    # summaryTitle/summaryText stay as the immutable model output). No issues -> both stay None, so
    # the summary is unchanged and unflagged. verify_summary is fail-safe (returns the originals on
    # any error). The title is audited because it is the first thing a client reads and it carries
    # dates and laterality that a body-only check never saw.
    verified_text = None
    verified_title = None
    verify_issues = None
    if verify:
        result = verify_summary(model, text, summary, title=title)
        if result["issues"]:
            verified_text = f"{doi_final} {result['fixed_text']}"
            verify_issues = result["issues"]
            # Decorated exactly like the stored title, so a verified title is a drop-in replacement
            # in every view; the export path strips the tags either way.
            fixed_title = (result.get("fixed_title") or "").strip()
            if fixed_title and fixed_title != title:
                verified_title = (
                    f"{manual_tag}{fixed_title}{diag_tag} (Pages {row['start']}-{row['end']})"
                )

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        # The body was cut off at the token budget: nothing is appended to the text (the report must
        # not carry a marker), but callers flag the row so the reviewer knows to check it.
        "truncated": truncated,
        "summaryText": f"{doi_final} {summary}",
        "verified": bool(verify),
        "verifiedText": verified_text,
        "verifiedTitle": verified_title,
        "verifyIssues": verify_issues,
        # The exact model input, so callers can persist the fine-tuning pair.
        "sourceText": text,
    }
