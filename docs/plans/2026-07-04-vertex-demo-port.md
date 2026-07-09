---
feature: vertex-demo-port
date: 2026-07-04
status: draft
base-branch: main
related-issues: []
---

# Vertex Demo Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the app's two Gemini call paths (segmentation `/getPages` + B5 categorization) through Vertex AI (BAA-covered) so a real-PHI, 100+ page case can be demoed end-to-end on Monday 2026-07-07.

**Architecture:** Replace the Developer-API-only Files API with inline byte-budgeted PDF chunks (Vertex has no `files.upload`; online requests cap at ~20 MB), pin the model per endpoint (`gemini-2.5-flash` on Vertex - the `-latest` aliases do not exist there), and wrap both `generate_content` call sites in the jittered-backoff retry proven live in `experiments/a1-segmentation/src/genai_client.py`. Client construction mirrors the experiment's env-driven routing so whichever auth mode the Vertex smoke already validated (API-key express or ADC) works unchanged.

**Tech Stack:** Flask, google-genai (Vertex AI + Developer API), pypdf, httpx, pytest.

## Global Constraints

- ASCII only; ruff clean (`uv run ruff check . && uv run ruff format .`); suite green (`uv run pytest`).
- NEVER commit PDFs, OCR text, page-map CSVs, or patient names; chunk files land under `uploads/` (gitignored).
- Claude must NOT read or edit `.env` (secrets hook + policy); Adrian applies `.env` changes himself.
- The one-path rule: after this port there is ONE PDF delivery mechanism (inline chunks) used by BOTH Vertex and the Developer API (both accept inline data; the Developer API path remains for non-PHI dev only).
- No scope creep: no chunk-seam accuracy fix (that is the experiment branch's ongoing work), no taxonomy curation (B6), no GCS path (express-mode API keys have no project for bucket access; inline chunks work under both auth modes).

## Context

- Demo: full end-to-end in the Flask UI (upload -> segment -> categorize -> review CSV -> summarize -> export Word), real PHI case, 100+ pages, on Adrian's Windows dev machine.
- Today: app 429s on the free-tier Developer API (10 RPM / 250 RPD on gemini-2.5-flash class models) and that endpoint has no BAA, so real PHI cannot legally transit it. The Vertex project HAS working billing (validated by the experiment's vertex_smoke/bake-off runs, commits 639f23e..e6a0713).
- A naive `GOOGLE_GENAI_USE_VERTEXAI=true` flip breaks the app twice: `genai_client.files.upload` raises "only supported in the Gemini Developer client" ([python-genai #2096](https://github.com/googleapis/python-genai/issues/2096)), and `gemini-flash-latest` 404s (Vertex has no `-latest` aliases; see `experiments/a1-segmentation/src/genai_client.py:26-31`).
- Summarization/extraction are OpenAI-only (`gpt-4o`/`gpt-4o-mini`) - untouched by this port. NOTE for Adrian (compliance, not code): confirm the OpenAI account has a BAA; the app has always sent OCR text there.

## Approach

- **Chosen:** env-driven client routing copied from the experiment + inline byte-budgeted chunks + retry wrapper. Smallest diff that makes the demo path legal and reliable; every risky piece is already proven live on Vertex by the bake-off code.
- **Rejected: GCS URI delivery.** Needs a bucket + project-bound auth (excludes API-key express mode), adds an upload step and lifecycle management; inline chunks impose no new infra and keep one code path. Revisit only if a single page exceeds the chunk budget.
- **Rejected: keep Files API for the dev path, branch per backend.** Two delivery paths to test and rehearse; the dev path also works inline. Deleted instead.
- **Rejected: paid tier on the Developer API.** Fixes 429s but not the missing BAA - unusable for a real-PHI demo.

---

### Task 1: Config + Vertex-aware client construction

**Files:**
- Modify: `mrr_ai/config.py` (append after `UPLOAD_FOLDER`)
- Modify: `mrr_ai/extensions.py`
- approach: code (env wiring; verified by the suite importing it + live smoke in Verification)

**Interfaces:**
- Produces: `config.USE_VERTEX: bool`, `config.GENAI_MODEL: str`, `config.GENAI_MAX_RETRIES: int`, `config.GENAI_RETRY_BASE_DELAY: float`, `config.GENAI_RETRY_MAX_DELAY: float`, `config.CHUNK_BUDGET_MB: float` - consumed by Tasks 2-5.

- [ ] **Step 1: Append routing/retry/chunk config to `mrr_ai/config.py`**

```python
# --- Gemini routing -------------------------------------------------------------------
# Vertex AI (aiplatform.googleapis.com) is covered by the Google Cloud BAA; the AI Studio
# Developer API is NOT. Any PHI processing requires GOOGLE_GENAI_USE_VERTEXAI=true.
USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Vertex has no "-latest" model aliases, so the default is chosen per endpoint.
GENAI_MODEL = os.environ.get("GENAI_MODEL") or (
    "gemini-2.5-flash" if USE_VERTEX else "gemini-flash-latest"
)

# Retry tuning for transient 429 (Vertex dynamic shared quota) and 5xx overload.
GENAI_MAX_RETRIES = int(os.environ.get("GENAI_MAX_RETRIES", 6))
GENAI_RETRY_BASE_DELAY = float(os.environ.get("GENAI_RETRY_BASE_DELAY", 2.0))
GENAI_RETRY_MAX_DELAY = float(os.environ.get("GENAI_RETRY_MAX_DELAY", 30.0))

# Inline-PDF chunk budget: Vertex online requests cap at ~20 MB total; per-page byte
# sums slightly overestimate real chunk size, so 12.5 MB stays conservatively under it.
CHUNK_BUDGET_MB = float(os.environ.get("CHUNK_BUDGET_MB", 12.5))
```

- [ ] **Step 2: Replace the client construction in `mrr_ai/extensions.py`**

```python
"""External service clients, created once and imported where needed.

env validation runs here so the clients are never built with missing secrets,
regardless of which module imports this first.
"""

import os

from google import genai
from openai import OpenAI

from mrr_ai import config
from mrr_ai.config import validate_env

validate_env()


def _build_client():
    """google-genai client routed per GOOGLE_GENAI_USE_VERTEXAI (see .env.example).

    Vertex AI is BAA-covered; the Developer API is NOT - PHI runs MUST set the flag.
    Auth on Vertex: GOOGLE_CLOUD_PROJECT set -> service-account/ADC (with optional
    GOOGLE_CLOUD_LOCATION, default "global"); unset -> the GCP API key in
    GEMINI_API_KEY against the Vertex endpoint. Mirrors the construction proven by
    experiments/a1-segmentation/src/genai_client.py.
    """
    if config.USE_VERTEX:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
            return genai.Client(vertexai=True, project=project, location=location)
        return genai.Client(vertexai=True, api_key=os.environ["GEMINI_API_KEY"])
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


genai_client = _build_client()
# OPENAI_API_KEY is read from the environment by the OpenAI client (see .env.example).
client = OpenAI()
```

- [ ] **Step 3: Run the suite to prove imports still work**

Run: `uv run pytest -q`
Expected: same pass count as baseline (conftest sets dummy keys; flag unset -> dev-API branch constructs offline).

- [ ] **Step 4: Commit**

```bash
git add mrr_ai/config.py mrr_ai/extensions.py
git commit -m "feat(sdk): route app genai client to Vertex AI when flagged" -- mrr_ai/config.py mrr_ai/extensions.py
```

---

### Task 2: Retry wrapper (`generate_with_retry`)

**Files:**
- Create: `mrr_ai/services/genai_retry.py`
- Test: `tests/unit/test_genai_retry.py`
- Modify: `pyproject.toml` (declare `httpx` - currently only a transitive dep of google-genai, now imported directly)
- approach: tdd (pure retry logic, deterministic with sleep patched)

**Interfaces:**
- Produces: `generate_with_retry(client, **kwargs) -> response` - the client is an explicit first argument so blueprints keep `genai_client` as their single monkeypatchable seam (existing tests patch `seg.genai_client`). Consumed by Tasks 4 and 5.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_genai_retry.py`:

```python
"""Unit tests for the jittered-backoff Gemini retry wrapper (no network, sleep patched)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from mrr_ai.services import genai_retry
from mrr_ai.services.genai_retry import generate_with_retry


def _flaky_client(failures):
    """Client whose generate_content raises each exception in `failures`, then succeeds."""
    remaining = list(failures)

    def generate_content(**kwargs):
        if remaining:
            raise remaining.pop(0)
        return SimpleNamespace(text="ok")

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(genai_retry.time, "sleep", lambda s: None)


def _server_error():
    # NOTE: verify the errors.ServerError constructor at build time with
    #   uv run python -c "import inspect; from google.genai import errors; print(inspect.signature(errors.APIError.__init__))"
    # and adapt these two factories if the signature differs.
    return errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})


def _client_error(code, message):
    return errors.ClientError(code, {"error": {"message": message, "status": "X"}})


def test_retries_transient_503_then_succeeds():
    client = _flaky_client([_server_error(), _server_error()])
    assert generate_with_retry(client, model="m", contents="c").text == "ok"


def test_retries_transient_429_then_succeeds():
    client = _flaky_client([_client_error(429, "rate limited")])
    assert generate_with_retry(client, model="m", contents="c").text == "ok"


def test_non_429_client_error_raises_immediately():
    client = _flaky_client([_client_error(404, "model not found")])
    with pytest.raises(errors.ClientError):
        generate_with_retry(client, model="m", contents="c")


def test_per_day_quota_fails_fast():
    client = _flaky_client([_client_error(429, "GenerateRequestsPerDayPerProject exceeded")])
    with pytest.raises(errors.ClientError):
        generate_with_retry(client, model="m", contents="c")


def test_exhausted_retries_raise_last_error():
    client = _flaky_client([_server_error()] * 99)
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="m", contents="c")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_genai_retry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mrr_ai.services.genai_retry'`

- [ ] **Step 3: Implement `mrr_ai/services/genai_retry.py`**

```python
"""Retry wrapper for google-genai generate_content calls.

Vertex gemini-2.5-flash runs on dynamic shared quota: under load it returns 429
RESOURCE_EXHAUSTED / 503 UNAVAILABLE, or drops the connection without a status. The
demo/production call path must ride those out with full-jitter exponential backoff
rather than crash. Adapted from experiments/a1-segmentation/src/genai_client.py,
which proved this behavior on live Vertex traffic.
"""

import random
import time

import httpx
from google.genai import errors

from mrr_ai.config import GENAI_MAX_RETRIES, GENAI_RETRY_BASE_DELAY, GENAI_RETRY_MAX_DELAY


def _backoff_delay(attempt):
    """Full-jitter exponential backoff delay in seconds for 0-indexed retry `attempt`.

    Random wait in [0, min(max_delay, base * 2**attempt)]: without jitter, parallel
    callers that all hit 429 retry in lockstep and re-collide on the shared quota pool.
    """
    ceiling = min(GENAI_RETRY_MAX_DELAY, GENAI_RETRY_BASE_DELAY * (2**attempt))
    return random.uniform(0.0, ceiling)


def generate_with_retry(client, **kwargs):
    """Call client.models.generate_content, retrying transient failures.

    Retries 5xx, transient 429, and transport disconnects. Re-raises immediately on
    non-429 client errors and on per-day/free-tier quota exhaustion (backoff cannot
    fix those inside a request's lifetime). The client is passed explicitly so route
    modules keep a single patchable client seam.
    """
    last = None
    for attempt in range(GENAI_MAX_RETRIES):
        try:
            return client.models.generate_content(**kwargs)
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry only transient 429 rate limiting
            if getattr(exc, "code", None) != 429:
                raise
            if "PerDay" in str(exc) or "free_tier" in str(exc):
                raise
            last = exc
        except httpx.TransportError as exc:  # disconnect without an HTTP status
            last = exc
        if attempt < GENAI_MAX_RETRIES - 1:
            time.sleep(_backoff_delay(attempt))
    raise last
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_genai_retry.py -v`
Expected: 5 passed. If the error constructors fail, run the inspect command from Step 1's note and adjust the two factory helpers (only the test file changes).

- [ ] **Step 5: Declare httpx and commit**

Run: `uv add httpx` (already in `uv.lock` transitively; this only promotes it to a direct dependency)

```bash
git add mrr_ai/services/genai_retry.py tests/unit/test_genai_retry.py pyproject.toml uv.lock
git commit -m "feat(sdk): add jittered retry wrapper for genai calls" -- mrr_ai/services/genai_retry.py tests/unit/test_genai_retry.py pyproject.toml uv.lock
```

---

### Task 3: Byte-budgeted PDF chunking

**Files:**
- Modify: `mrr_ai/services/pdf.py` (add `io` import, `page_raw_sizes`, `segment_pdf_by_bytes`; keep existing functions - `segment_pdf` still serves `/segmentPDF`)
- Test: `tests/unit/test_pdf.py` (append)
- approach: tdd (pure function over synthetic PDFs)

**Interfaces:**
- Consumes: `UPLOAD_FOLDER` (existing module global, monkeypatched in tests).
- Produces: `segment_pdf_by_bytes(input_pdf, budget_mb=12.5, max_pages=100) -> list[tuple[str, int]]` - ordered `(chunk_path, page_count)` pairs; raises `RuntimeError` if one page exceeds the budget. Consumed by Task 4.

- [ ] **Step 1: Write the failing tests (append to `tests/unit/test_pdf.py`)**

```python
def _one_blank_page_bytes():
    """Raw size of a single blank page as its own PDF, measured not assumed."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getbuffer().nbytes


def test_segment_pdf_by_bytes_single_chunk(make_pdf, tmp_path, monkeypatch):
    from mrr_ai.services import pdf as pdf_service

    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "up"))
    path = make_pdf(tmp_path / "small.pdf", pages=3)

    chunks = pdf_service.segment_pdf_by_bytes(path, budget_mb=12.5, max_pages=100)

    assert [count for _p, count in chunks] == [3]
    assert all(str(tmp_path / "up") in p for p, _c in chunks)


def test_segment_pdf_by_bytes_respects_max_pages(make_pdf, tmp_path, monkeypatch):
    from mrr_ai.services import pdf as pdf_service

    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "up"))
    path = make_pdf(tmp_path / "five.pdf", pages=5)

    chunks = pdf_service.segment_pdf_by_bytes(path, budget_mb=12.5, max_pages=2)

    assert [count for _p, count in chunks] == [2, 2, 1]


def test_segment_pdf_by_bytes_respects_byte_budget(make_pdf, tmp_path, monkeypatch):
    from mrr_ai.services import pdf as pdf_service

    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "up"))
    path = make_pdf(tmp_path / "three.pdf", pages=3)
    one_page_mb = _one_blank_page_bytes() / 1048576

    # Budget fits one page but not two -> one page per chunk.
    chunks = pdf_service.segment_pdf_by_bytes(path, budget_mb=one_page_mb * 1.5, max_pages=100)

    assert [count for _p, count in chunks] == [1, 1, 1]


def test_segment_pdf_by_bytes_oversized_page_fails_fast(make_pdf, tmp_path, monkeypatch):
    import pytest

    from mrr_ai.services import pdf as pdf_service

    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "up"))
    path = make_pdf(tmp_path / "one.pdf", pages=1)
    one_page_mb = _one_blank_page_bytes() / 1048576

    with pytest.raises(RuntimeError, match="larger than"):
        pdf_service.segment_pdf_by_bytes(path, budget_mb=one_page_mb * 0.5, max_pages=100)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pdf.py -v -k by_bytes`
Expected: FAIL with `AttributeError: ... has no attribute 'segment_pdf_by_bytes'`

- [ ] **Step 3: Implement in `mrr_ai/services/pdf.py`** (add `import io` at the top)

```python
def page_raw_sizes(reader):
    """Per-page byte size of each page written as its own single-page PDF.

    A multi-page chunk's real size is slightly LESS than the sum of these (per-page
    structural overhead is not shared), so greedy packing against a budget stays
    conservatively under the inline request cap.
    """
    sizes = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        sizes.append(buffer.getbuffer().nbytes)
    return sizes


def segment_pdf_by_bytes(input_pdf, budget_mb=12.5, max_pages=100):
    """Split a PDF into consecutive chunks bounded by BOTH a byte budget and a page cap.

    Chunks are delivered inline to Gemini (Vertex has no Files API; online requests cap
    at ~20 MB), so a fixed page count cannot bound request size when page byte-density
    varies. Returns ordered (chunk_path, page_count) pairs; the caller accumulates page
    offsets from the real counts. Fails fast if a single page exceeds the budget.
    """
    budget_bytes = int(budget_mb * 1024 * 1024)
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)
    sizes = page_raw_sizes(reader)
    base_name = os.path.splitext(os.path.basename(input_pdf))[0]
    output_folder = os.path.join(UPLOAD_FOLDER, f"{base_name}_segmented")
    os.makedirs(output_folder, exist_ok=True)

    chunks = []
    start = 0  # 0-based page index
    while start < total_pages:
        if sizes[start] > budget_bytes:
            raise RuntimeError(
                f"page {start + 1} is {sizes[start] / 1048576:.1f} MB raw, larger than the "
                f"{budget_mb} MB chunk budget; raise CHUNK_BUDGET_MB or split the source PDF"
            )
        end, acc = start, 0
        while end < total_pages and end - start < max_pages and acc + sizes[end] <= budget_bytes:
            acc += sizes[end]
            end += 1
        writer = PdfWriter()
        for page_num in range(start, end):
            writer.add_page(reader.pages[page_num])
        chunk_path = os.path.join(output_folder, f"{base_name}_{len(chunks) + 1:02}.pdf")
        with open(chunk_path, "wb") as handle:
            writer.write(handle)
        chunks.append((chunk_path, end - start))
        start = end
    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pdf.py -v`
Expected: all pass (new 4 + existing).

- [ ] **Step 5: Commit**

```bash
git add mrr_ai/services/pdf.py tests/unit/test_pdf.py
git commit -m "feat(segmentation): add byte-budgeted pdf chunking for inline delivery" -- mrr_ai/services/pdf.py tests/unit/test_pdf.py
```

---

### Task 4: Rewire `/getPages` to inline chunks; delete the Files API path

**Files:**
- Modify: `mrr_ai/blueprints/segmentation.py`
- Modify: `mrr_ai/services/gemini.py` (delete `upload_to_gemini`, `wait_for_files_active`, the `time` import, and the now-unused `genai_client` import; keep `SEGMENTATION_PROMPT` + `parse_segment_item`)
- Test: `tests/integration/test_segmentation.py`, `tests/unit/test_gemini.py`
- approach: test-after (route wiring; existing integration suite is the harness)

**Interfaces:**
- Consumes: `generate_with_retry(client, **kwargs)` (Task 2), `segment_pdf_by_bytes(...) -> list[(path, count)]` (Task 3), `config.GENAI_MODEL`, `config.CHUNK_BUDGET_MB` (Task 1).
- Produces: unchanged route contract - `/getPages` returns `{"pages": "<csv lines>"}`.

- [ ] **Step 1: Rewrite the Gemini call + chunk loop in `mrr_ai/blueprints/segmentation.py`**

Imports change to:

```python
"""Gemini-driven sub-document segmentation routes."""

import json

from flask import Blueprint, request
from google.genai import types

from mrr_ai import state
from mrr_ai.config import CHUNK_BUDGET_MB, GENAI_MODEL
from mrr_ai.extensions import genai_client
from mrr_ai.services.classification import classify
from mrr_ai.services.gemini import SEGMENTATION_PROMPT, parse_segment_item
from mrr_ai.services.genai_retry import generate_with_retry
from mrr_ai.services.ocr import extract_text_from_selected_pages
from mrr_ai.services.pdf import segment_pdf, segment_pdf_by_bytes
```

`_segment_one_pdf` becomes (drop the `segmentation_model` parameter):

```python
def _segment_one_pdf(pdf_path, generation_config):
    """Send one PDF chunk inline to Gemini and return its parsed segment JSON array.

    Inline bytes rather than the Files API: the Files API exists only on the Developer
    API endpoint, and chunks are byte-budgeted upstream to fit Vertex's ~20 MB online
    request cap (the Developer API accepts the same inline form).
    """
    with open(pdf_path, "rb") as handle:
        pdf_part = types.Part.from_bytes(data=handle.read(), mime_type="application/pdf")

    response = generate_with_retry(
        genai_client,
        model=GENAI_MODEL,
        contents=[pdf_part, SEGMENTATION_PROMPT],
        config=generation_config,
    )
    clean_response = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean_response)
```

In `getPages()`, replace everything from `pdf_size = get_pdf_size(...)` through the end of the chunk loop with:

```python
    generation_config = types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        top_k=40,
        response_mime_type="application/json",
        system_instruction="You are an assistant that segments a large document into subdocuments and provide their metadata.",
    )

    # Every PDF goes through byte-budgeted chunking (a small PDF yields one chunk), so
    # Vertex and the Developer API share one inline delivery path. Chunk page numbers
    # are local (1..count) and shifted by current_offset to recover absolute pages.
    try:
        chunks = segment_pdf_by_bytes(
            state.pdf_filepath, budget_mb=CHUNK_BUDGET_MB, max_pages=page_delimiter
        )
    except RuntimeError as exc:
        print(f"Chunking failed: {exc}")
        return {"pages": str(exc)}
    state.sorted_file_paths = [path for path, _count in chunks]

    current_offset = 0
    lines = []
    for pdf_path, page_count in chunks:
        print(f"Working on: {pdf_path}")
        try:
            segments = _segment_one_pdf(pdf_path, generation_config)
        except Exception as e:
            print(f"An error occurred while sending the message: {e}")
            return {"pages": str(e)}

        for item in segments:
            try:
                lines.append(_format_segment_line(item, current_offset))
            except (KeyError, TypeError, ValueError) as exc:
                print(f"Skipping malformed segment: {exc}")
                continue

        current_offset += page_count
```

Delete the now-unused `get_pdf_size`/`get_pdf_page_count`/`segment_pdf_locally` imports (keep `segment_pdf` - `/segmentPDF` still uses it). The offset now accumulates REAL chunk page counts, which also removes the latent bug where a short final chunk would still advance the offset by the full delimiter.

- [ ] **Step 2: Strip the Files API from `mrr_ai/services/gemini.py`**

The file keeps only its docstring (update to "Gemini segmentation: prompt and response parsing."), `SEGMENTATION_PROMPT`, and `parse_segment_item`. Delete `upload_to_gemini`, `wait_for_files_active`, `import time`, and `from mrr_ai.extensions import genai_client`.

- [ ] **Step 3: Update the tests**

In `tests/integration/test_segmentation.py`, `_patch_gemini` shrinks to (and gains chunk-dir redirection so small-PDF tests no longer write into the repo's `uploads/`):

```python
def _patch_gemini(monkeypatch, fake_client, tmp_path):
    """Stub the genai client and point chunk output at a temp dir."""
    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "chunks"))
    monkeypatch.setattr(seg, "genai_client", fake_client)
```

Update every `_patch_gemini(monkeypatch, fake_genai(...))` call site to pass `tmp_path`, and drop the now-redundant standalone `monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", ...)` line in `test_get_pages_large_pdf_batches_with_offset` (the helper does it now). Assertions are unchanged: blank test pages are tiny, so the byte budget never binds and 101 pages with delimiter 100 still split [100, 1].

In `tests/unit/test_gemini.py`, delete the `upload_to_gemini` / `wait_for_files_active` tests and their imports; keep all `parse_segment_item` tests.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, none skipped unexpectedly. If `types.Part.from_bytes` chokes on the fake client path, it does not touch the network - it only wraps bytes - so failures here mean a real wiring bug: fix, do not mock around it.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check . && uv run ruff format .
git add mrr_ai/blueprints/segmentation.py mrr_ai/services/gemini.py tests/integration/test_segmentation.py tests/unit/test_gemini.py
git commit -m "feat(segmentation): deliver pdf chunks inline, drop dev-only files api" -- mrr_ai/blueprints/segmentation.py mrr_ai/services/gemini.py tests/integration/test_segmentation.py tests/unit/test_gemini.py
```

---

### Task 5: Categorization LLM stage on the routed model + retries

**Files:**
- Modify: `mrr_ai/services/classification.py`
- Test: `tests/unit/test_classification.py` (adjust only if it asserts the literal model name)
- approach: test-after (existing unit tests cover `classify`; this is model/transport wiring)

**Interfaces:**
- Consumes: `generate_with_retry` (Task 2), `config.GENAI_MODEL` (Task 1).
- Produces: unchanged - `classify(title, page_text=None) -> Classification`.

- [ ] **Step 1: Swap the pinned alias and wrap the call**

In `mrr_ai/services/classification.py`:
- Delete `_LLM_MODEL = "gemini-flash-latest"`.
- Add `from mrr_ai.config import GENAI_MODEL` and `from mrr_ai.services.genai_retry import generate_with_retry`.
- In `llm_classify`, replace the call:

```python
    try:
        response = generate_with_retry(
            genai_client, model=GENAI_MODEL, contents=prompt, config=config
        )
    except Exception as exc:
        print(f"LLM classification failed: {exc}")
        return None
```

(The broad except stays: a dead LLM must degrade to embedding-only + manual flag, never 500 the route.)

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/unit/test_classification.py -v && uv run pytest -q`
Expected: all pass. If a test asserts `_LLM_MODEL` or the literal string `gemini-flash-latest`, update it to reference `mrr_ai.config.GENAI_MODEL`.

- [ ] **Step 3: Commit**

```bash
git add mrr_ai/services/classification.py tests/unit/test_classification.py
git commit -m "feat(categorization): route llm stage via configured model with retries" -- mrr_ai/services/classification.py tests/unit/test_classification.py
```

---

### Task 6: Docs + env reference

**Files:**
- Modify: `docs/architecture.md`, `.env.example`
- approach: code (docs-as-code, same PR)

- [ ] **Step 1: Update `docs/architecture.md`**

- In "External dependencies & PHI data flow": replace the "whole PDF is uploaded" Gemini bullet with: PDFs are sent as inline byte-budgeted chunks; with `GOOGLE_GENAI_USE_VERTEXAI=true` all Gemini traffic uses Vertex AI (BAA-covered) - REQUIRED for PHI; the Developer API path remains for non-PHI development only.
- In "Known limitations": replace the fixed "100-page chunks" sentence with the byte-budgeted chunking description (seam mis-split limitation still stands; the experiment work owns the fix).
- In "The pipeline" step 1: drop the "uploads the PDF to Gemini" wording (now "sends the PDF to Gemini in inline chunks").

- [ ] **Step 2: Update `.env.example`**

In the Vertex block, note that the APP now honors the flag (not just the experiment), and document `GENAI_MODEL` (default `gemini-2.5-flash` on Vertex / `gemini-flash-latest` otherwise), `GENAI_MAX_RETRIES`, and `CHUNK_BUDGET_MB` (default 12.5).

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md .env.example
git commit -m "docs(pipeline): document vertex routing and inline chunk delivery" -- docs/architecture.md .env.example
```

---

## Risk / Rollback

- **Blast radius:** `/getPages` (segmentation + categorization) and the B5 LLM stage. Summarize/export/extraction (OpenAI) and the manual-CSV path are untouched, so the fallback demo path cannot be broken by this change.
- **Biggest unknown:** which Vertex auth mode the smoke validated (Adrian wasn't sure). The client mirrors the experiment exactly, so whatever `.env` made `vertex_smoke.py` pass makes the app pass. Verification step 1 settles it before any UI testing.
- **Latency risk:** a 100+ page case = N chunk calls + up to ~1 classify call per sub-document, sequential. Rehearsal (Verification step 5) times it; if too slow for a live audience, the rehearsal's saved CSV via `/uploadAndCheckCSV` is the stage plan and the live run becomes the "here's it actually running" teaser.
- **Rollback:** `git revert` the feature branch commits (or demo from `main`); with `GOOGLE_GENAI_USE_VERTEXAI` unset the app behaves as before on the Developer API (inline chunks work there too).

## Verification (live gate, Adrian in the loop)

1. **Adrian edits `.env`** (Claude must not): set `GOOGLE_GENAI_USE_VERTEXAI=true` plus whichever auth vars the experiment runs used (`GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`/`GOOGLE_APPLICATION_CREDENTIALS` for ADC, or nothing extra for the API-key path).
2. **Smoke the auth**: `uv run python experiments/a1-segmentation/src/vertex_smoke.py` -> a successful generate call proves routing + billing before touching the app.
3. **Gates**: `uv run pytest -q` all green; `uv run ruff check . && uv run ruff format .` clean; `uv run pre-commit run --all-files` (gitleaks) clean.
4. **Synthetic end-to-end** (no PHI): start `uv run python app.py`, upload a small synthetic PDF, run segment -> categorize; confirm plausible CSV rows and no 429/404 in the console. This also downloads/warms the MiniLM embedding model (~90 MB, one-time) so the demo never does a cold download.
5. **Real-case rehearsal** (PHI, Vertex only): upload the actual demo PDF; run the FULL flow (segment -> categorize -> review CSV -> summarize -> export Word). Record wall-clock time per stage. Verify the Word output opens and reads sane.
6. **Canned backup**: save the rehearsal's page-map CSV OUTSIDE the repo (e.g. `~/MRRs/`); rehearse loading it via the manual-CSV upload path so the demo has a zero-AI-dependency plan B.
7. **Hygiene**: `git status` shows no PDFs/CSVs/OCR text staged; `uploads/` stayed gitignored.
