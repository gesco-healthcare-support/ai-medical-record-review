# ADR-0002: Migrate to google-genai and pypdf

**Status:** Accepted

## Context
`google-generativeai` is deprecated (no Python 3.13 support, frozen). `PyPDF2` is
unmaintained; its `PdfMerger` was removed in pypdf 5.x.

## Decision
Migrate Gemini calls to the unified **`google-genai`** SDK (`genai.Client`,
`files.upload`/`files.get`, `models.generate_content`) and PDF handling to **`pypdf`**
(`PdfWriter.append` replaces `PdfMerger`). Behavior preserved.

## Alternatives
- Stay on the deprecated SDKs (blocks 3.13, accrues risk) - rejected.
- Drop to the low-level `google-ai-generativelanguage` - more work than the unified SDK.

## Consequences
- On actively maintained libraries; unblocks a future Python 3.13 bump.
- Gemini call shape changed from a chat session to `generate_content(contents=[file, prompt])`.
- Verified with a live upload + generate_content smoke and a pypdf merge smoke.
