"""Disk-backed verdict cache for the boundary oracles: a long guarded run RESUMES instead of
restarting. Every adjacent / range_probe answer (with its token cost) is appended to a JSONL the
moment it is computed, so a crash, a 429 that exhausts retries, or a deliberate stop between the
sol2 and sol4b runs loses nothing -- re-running replays cached answers with zero Gemini spend.

Determinism: the oracles call at temperature 0, so a cached answer is the same answer the model
would give again; pinning it also makes a resumed run reproducible.

HIPAA: the cache FILE is named by a hash of the PDF path (never the patient-named ROR filename),
and the cached VALUES are only NEW/SAME/SAME_DOC/NEW_DOC labels + token counts -- no PHI on disk.
"""

import hashlib
import json
import os
import threading
from types import SimpleNamespace

from config import OUTPUTS

CACHE_DIR = os.environ.get("GENAI_CACHE_DIR", os.path.join(OUTPUTS, "oracle-cache"))
ENABLED = os.environ.get("GENAI_CACHE", "1").strip().lower() in ("1", "true", "yes")

_lock = threading.Lock()
_mem = {}        # pdf_key -> {cache_key: {"v": verdict, "in": in_tok, "out": out_tok}}
_loaded = set()  # pdf_keys whose JSONL has already been read into _mem


def _pdf_key(pdf_path):
    """Filesystem-safe, PHI-free identity for the source PDF: a short hash of its absolute path
    plus its byte size (so a changed file never reuses stale answers)."""
    if pdf_path is None:
        return "_nopdf"
    ap = os.path.abspath(str(pdf_path))
    try:
        size = os.path.getsize(ap)
    except OSError:
        size = 0
    return hashlib.sha256(f"{ap}|{size}".encode()).hexdigest()[:16]


def _file_for(pdf_key):
    return os.path.join(CACHE_DIR, pdf_key + ".jsonl")


def _load(pdf_key):
    if pdf_key in _loaded:
        return
    _mem.setdefault(pdf_key, {})
    path = _file_for(pdf_key)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                _mem[pdf_key][rec["k"]] = {"v": rec["v"], "in": rec["in"], "out": rec["out"]}
    _loaded.add(pdf_key)


def cached(pdf_path, oracle, args, dpi, cost, compute):
    """Return the cached verdict for (pdf, oracle, args, model, dpi), else run ``compute()`` and
    persist its answer. ``compute`` is a zero-arg callable that performs the real Gemini call
    (recording tokens on ``cost``) and returns the verdict string (or None).

    On a HIT the recorded token cost is replayed into ``cost`` -- including the +1 call count, via
    Cost.add -- so a resumed run reports the same #calls / $ as a fresh one. On a MISS the tokens
    that ``compute`` added to ``cost`` are captured as a before/after delta.
    """
    if not ENABLED:
        return compute()

    import genai_client  # local import avoids a module-load cycle (genai_client <-> oracles)

    pdf_key = _pdf_key(pdf_path)
    cache_key = f"{oracle}|{args}|{genai_client.MODEL}|{dpi}"
    with _lock:
        _load(pdf_key)
        hit = _mem[pdf_key].get(cache_key)
    if hit is not None:
        cost.add(SimpleNamespace(prompt_token_count=hit["in"], candidates_token_count=hit["out"]))
        return hit["v"]

    in0, out0 = cost.input_tokens, cost.output_tokens
    verdict = compute()
    rec = {"k": cache_key, "v": verdict,
           "in": cost.input_tokens - in0, "out": cost.output_tokens - out0}
    with _lock:
        _mem[pdf_key][cache_key] = {"v": rec["v"], "in": rec["in"], "out": rec["out"]}
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_file_for(pdf_key), "a", encoding="utf-8") as fh:  # append-only = crash-safe
            fh.write(json.dumps(rec) + "\n")
    return verdict
