"""Cheap pre-flight token estimates, so the pacer can charge a request what it is likely to cost.

Why estimate at all: a provider that meters tokens rather than requests treats one 30,000-token
segmentation window very differently from a 500-token title call. Charging both "1 request" makes
the pacer wrong in whichever direction the traffic mix happens to lean.

Why ESTIMATE rather than count exactly: the count is only knowable after the call, and the pacer has
to decide before it. The estimate admits the request; ``reconcile`` corrects the bucket afterwards
from the provider's own reported usage, so a systematically wrong estimate cannot drift forever.

Every constant here was measured on 2026-08-03 rather than assumed - see
``W:\\MRR_Research_and_Analysis\\03_Reports\\OPENAI_OPTION_2026-08-03.md``.
"""

from app.services.llm.parts import DocumentPart, ImagePart, Part, TextPart

# 8,810 chars of system prompt + 40,040 chars of filler measured at 9,474 prompt tokens on
# gpt-5.4-mini => 5.16 chars/token. Rounded DOWN to 4.0 so the estimate errs high: admitting too
# few requests is a slowdown, admitting too many is a 429 storm.
_CHARS_PER_TOKEN = 4.0

# One rasterized US-Letter page at 120 dpi (the summary_image_dpi the pipeline actually sends),
# measured per provider. The GPT-4 family varies enormously - gpt-4o-mini charged 25,503 for the
# same image gpt-4o charged 767 - so a single constant across vendors would be badly wrong.
_IMAGE_TOKENS = {
    "openai": 2200,  # mid-range of the measured 767 (4.1/4o) .. 3,319 (4.1-nano); 5.x is 1,624
    "gemini": 1300,  # not directly measured; Gemini bills images near its 258-token page unit x tiles
}
_IMAGE_TOKENS_DEFAULT = 2200

# One PDF page sent inline to Gemini measured at 259 tokens (10-page synthetic PDF -> 2,589).
_PDF_PAGE_TOKENS = 259
# A PDF part carries no page count without parsing it, and parsing to price it would cost more than
# the estimate is worth. Assume a window-sized document; reconcile() corrects it from real usage.
_PDF_ASSUMED_PAGES = 100


def estimate_tokens(parts: list[Part], system: str | None = None, provider: str = "gemini") -> int:
    """Approximate input tokens for one request. Always >= 1, never raises.

    Deliberately biased high (chars/4 rather than the measured chars/5.16): under-charging the pacer
    is what produces a 429 storm, and over-charging only costs a little throughput.
    """
    total = len(system or "") / _CHARS_PER_TOKEN
    image_cost = _IMAGE_TOKENS.get(provider, _IMAGE_TOKENS_DEFAULT)
    for part in parts:
        if isinstance(part, TextPart):
            total += len(part.text) / _CHARS_PER_TOKEN
        elif isinstance(part, ImagePart):
            total += image_cost
        elif isinstance(part, DocumentPart):
            total += _PDF_PAGE_TOKENS * _PDF_ASSUMED_PAGES
    return max(1, int(total))
