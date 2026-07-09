"""markitdown page -> markdown conversion for Solution 3 (markitdown is mandatory here).

These records are SCANNED (no text layer), so markitdown's default pdfminer path returns blank
-- it must use the markitdown-ocr plugin, which is LLM-vision OCR via the same llm_client /
llm_model pattern markitdown uses for image descriptions (verified against the official
microsoft/markitdown README, 2026-06-16). With no llm_client, OCR is skipped and scanned pages
convert to empty markdown.

This module wires that backend from the environment so the moment a paid vision key exists the
solution runs; it does NOT call any LLM at import or in config_check. Backends (env
MARKITDOWN_OCR_BACKEND):
  openai -> OpenAI(api_key=OPENAI_API_KEY),                         model MARKITDOWN_LLM_MODEL or gpt-4o
  gemini -> OpenAI(api_key=GEMINI_API_KEY, base_url=<gemini openai-compat>),
                                                                    model MARKITDOWN_LLM_MODEL or gemini-flash-latest
  none / unset -> OCR skipped (the current credit-less state).
Cost reality on scans: markitdown OCR is ~1 LLM-vision pass/page, then sol3's boundary call is
another pass -> ~2x LLM passes/page; the "text is cheap" rationale only holds for born-digital
PDFs. Imports are lazy so the other three solutions never depend on markitdown.

  python src/markdown.py config_check   # report the configured backend (no network call)
"""

import os
import sys
import tempfile

from dotenv import load_dotenv
from pypdf import PdfReader, PdfWriter

# The app's .env holds OPENAI_API_KEY / GEMINI_API_KEY (same source the rest of the spike uses).
load_dotenv(r"P:\MRR_AI_Source\mrr-line_source\.env")

# Gemini's OpenAI-compatible endpoint (lets the OpenAI client talk to a Gemini vision model).
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_converter = None


def _build_llm_client():
    """Return (llm_client, llm_model) for the configured OCR backend, or (None, None) if OCR is
    off or the backend's key is missing. Instantiates only the OpenAI client wrapper (no network
    call happens until a conversion runs)."""
    backend = os.environ.get("MARKITDOWN_OCR_BACKEND", "none").lower()
    model = os.environ.get("MARKITDOWN_LLM_MODEL")
    if backend == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            return None, None
        from openai import OpenAI

        return OpenAI(api_key=os.environ["OPENAI_API_KEY"]), model or "gpt-4o"
    if backend == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            return None, None
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_OPENAI_BASE_URL)
        return client, model or "gemini-flash-latest"
    return None, None


def backend_summary():
    """One-line description of the configured backend, reading env only (no client, no network,
    never prints the key value)."""
    backend = os.environ.get("MARKITDOWN_OCR_BACKEND", "none").lower()
    if backend == "openai":
        key = "set" if os.environ.get("OPENAI_API_KEY") else "MISSING"
        return f"openai  model={os.environ.get('MARKITDOWN_LLM_MODEL', 'gpt-4o')}  OPENAI_API_KEY={key}"
    if backend == "gemini":
        key = "set" if os.environ.get("GEMINI_API_KEY") else "MISSING"
        return (f"gemini  model={os.environ.get('MARKITDOWN_LLM_MODEL', 'gemini-flash-latest')}  "
                f"GEMINI_API_KEY={key}  base_url={GEMINI_OPENAI_BASE_URL}")
    return "none (OCR skipped -> scanned pages convert to empty markdown)"


def _get_converter():
    global _converter
    if _converter is None:
        from markitdown import MarkItDown

        # enable_plugins picks up markitdown-ocr; the (client, model) drive its LLM-vision OCR.
        llm_client, llm_model = _build_llm_client()
        _converter = MarkItDown(enable_plugins=True, llm_client=llm_client, llm_model=llm_model)
    return _converter


def page_markdown(pdf_path, page_number):
    """Convert one 1-indexed page to markdown via markitdown (+ OCR plugin for scans)."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.add_page(reader.pages[page_number - 1])
    tmp = os.path.join(tempfile.gettempdir(), f"pss_md_{page_number}.pdf")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    return _get_converter().convert(tmp).text_content


if __name__ == "__main__":
    if sys.argv[1:2] == ["config_check"]:
        print(f"markitdown OCR backend: {backend_summary()}")
    else:
        print("usage: python src/markdown.py config_check")
