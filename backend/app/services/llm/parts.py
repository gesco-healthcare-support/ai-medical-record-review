"""Provider-neutral request parts.

The three summarize call sites currently hand `google.genai` types straight to the client, which is
why moving one model meant rewriting them. These types carry the same information with no SDK in
them, so a provider translates at its own boundary and nothing above the boundary knows which
vendor is answering.

Deliberately minimal: text, an image, and a whole document (a PDF). That is everything the pipeline
actually sends. A part type nobody sends is a part type nobody has tested.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextPart:
    """A span of prompt text - OCR output, an instruction, a rendered header."""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """One rasterized page. `data` is the encoded image itself, not a path or a URL.

    Both providers want the bytes, but in different wrappers: Gemini takes a Part built from bytes,
    OpenAI takes a base64 data URL. Keeping bytes here means neither encoding leaks upward.
    """

    data: bytes
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class DocumentPart:
    """A whole document sent inline, e.g. a PDF window for segmentation or DOI extraction.

    Present because the pipeline genuinely sends PDFs, not because summarization needs it - the
    segmentation and DOI paths stay on Gemini for now but go through the same interface, so the type
    has to exist for them to be expressible.
    """

    data: bytes
    mime_type: str = "application/pdf"


Part = TextPart | ImagePart | DocumentPart
