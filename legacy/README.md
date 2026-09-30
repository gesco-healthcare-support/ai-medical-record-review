# legacy/ - documents kept from the pre-rewrite Flask application

**The Flask app's code was removed on 2026-09-30.** Only its documents remain here, for reference. Nothing in this
folder describes the system that runs now: that is `backend/` (FastAPI) and `frontend/` (Next.js).

## What is here

`docs/` - the old app's documentation, kept because live pages link into it and because it holds source material
from the business:

- `docs/reference/`: the 2025 category taxonomy (`Categories Jan 25, 2025.docx`), the `MRR Steps.docx` guide, the
  summary macros, and the old API and CSV references. The two `.docx` files are tracked on purpose (`.gitignore`
  exempts `legacy/docs/reference/*.doc(x)` from its blanket rule);
- `docs/decisions/`: the old app's decision records, linked from `docs/explanation/categorization.md` and
  `docs/explanation/segmentation.md`;
- `docs/explanation/`, `docs/how-to/`, `docs/research/`, `docs/prompts/`: how the old app worked and was built.

## Where the code went

The app (`legacy/app.py`, `legacy/mrr_ai/`, `legacy/tests/`, `legacy/Dockerfile`), its entry point `serve.py` and
the repository-root `pyproject.toml` were removed on 2026-09-30. They were dead code - nothing imported or ran them -
and the root `pyproject.toml` made GitHub's dependency submission raise security alerts for the old app's
dependencies. The last `main` commit that contains all of it is `f2ed6b3`:

```bash
git show f2ed6b3:legacy/app.py
git checkout f2ed6b3 -- legacy/mrr_ai   # restore a folder into your working tree, if you need to read it locally
```

Several backend modules still carry "ported from mrr_ai/..." comments; that commit is where those paths resolve.

The repository-wide ruff settings that lived in the root `pyproject.toml` are now in `ruff.toml` at the root.
