# legacy/docs - archived documentation

Historical pages moved here from `docs/` on 2026-09-27. **Nothing here describes the current
app** and nothing here is maintained. The current documentation is the site built from `docs/`
(see [`docs/index.md`](../../docs/index.md)).

| path | what it was |
| --- | --- |
| `architecture.md`, `explanation/`, `how-to/`, `reference/*.md` | Pages written for the pre-rewrite Flask app (port 5010, blueprints, the page-map CSV between stages). |
| `decisions/` | Architecture decision records 0001-0006 from June-July 2026. Their still-true reasoning now lives in the current explanation pages. |
| `research/` | The original June 2026 research on segmentation, OCR and summarization. |
| `prompts/` | Prompts used to drive the July 2026 frontend conversion. |
| `reference/prompts/`, `reference/macros/`, `reference/*.docx` | The 2025 per-category prompt sources, the Word output macros, the category taxonomy and the MRR steps guide. The live prompts are `backend/app/services/prompts.py` plus any admin-edited rows in the database. |

Git history keeps every earlier version. Read these for background only; check any claim against
the code before relying on it.
