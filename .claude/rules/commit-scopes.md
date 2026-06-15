# Commit Scopes (MRR AI)

Allowed commit/PR scopes for this repo (kebab-case). Keeps history grep-able.

- `repo` - repo setup, meta, top-level config
- `tooling` - uv, build, dependency, env tooling
- `quality` - linters, formatters, pre-commit, CI gates
- `ci` - GitHub Actions / pipeline
- `docs` - documentation, runbook, references
- `sdk` - third-party SDK swaps (gemini/openai/pdf libs)
- `segmentation` - sub-document boundary detection (getPages)
- `categorization` - category assignment (taxonomy, matching, cascade)
- `summarize` - OpenAI summarization + prompts
- `ocr` - Tesseract / Poppler / text extraction
- `export` - Word/CSV output generation
- `pipeline` - cross-cutting flow / orchestration
- `ui` - templates / static

If a change does not fit, add the scope here in the same PR.
