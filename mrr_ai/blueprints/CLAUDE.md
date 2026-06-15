# blueprints

HTTP routes grouped by area (26 routes total). Each module defines `bp = Blueprint(...)`
and is registered in `__init__.py:register_blueprints`.

Modules: `pages` (8 GET + `/reset`), `upload` (3), `segmentation` (`/getPages`,
`/segmentPDF`), `summarize` (`/summarize`, `/summarize_indiv_record`), `reports`
(`/getDiagOpRep`, `/getDepoRep`, `/compute_page_ranges`), `export` (3), `extraction`
(`/getpatientnameanddob`, `/getlawfirm`), `individual_mrr` (2). Full list:
`../../docs/reference/api-routes.md`.

## Conventions
- Keep routes **thin**; put logic in `mrr_ai/services/` (no Flask there, so it's testable).
- Shared state: `from mrr_ai import state` then `state.x` (read/write). Never `global`,
  never `from mrr_ai.state import x`.
- Upload folder: `current_app.config["UPLOAD_FOLDER"]` (not a hardcoded path).
- LLM clients: `from mrr_ai.extensions import client, genai_client`.
- Many routes depend on state set by a prior call (e.g. `/upload` -> `/summarize`), so the
  app is **single-process**.
- PHI flows to OpenAI/Gemini in these routes; do **not** add logging of PDF/OCR/LLM bodies.

Add a route: `../../docs/how-to/add-a-blueprint.md`.
