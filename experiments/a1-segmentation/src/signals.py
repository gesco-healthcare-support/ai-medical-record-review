"""Local boundary signals: cheap, deterministic evidence for targeting verification.

Each signal inspects the SAME pages the segmenter LLM saw and answers "does the physical
evidence agree with the LLM's boundary?" - so suspicion can be COMPUTED on unseen PDFs with
no ground truth (the model's self-reported confidence was measured useless, 231/232 'high').

Data sources (all local, PHI never leaves the machine):
  - Tesseract OCR text per page (cache/ocr/<case>.jsonl - already built; production OCRs anyway)
  - per-page raw byte sizes (same computation the chunker uses)
  - low-DPI page renders for a difference-hash (perceptual) visual distance

Signals per transition page p (2..n), each -> (pro_start, anti_start) evidence:
  pagenum   printed "page K of M": K == 1 is pro-start; K > 1 is anti-start (continuation)
  banner    fax/transmission banner fingerprint on the top lines: same banner run = anti-start,
            banner change (both pages bannered) = pro-start
  header    similarity of the first non-blank lines (digit-stripped): high = anti-start
  dates     shared calendar dates across the transition = anti-start; new-only dates = weak pro
  bytes     relative page byte-size jump: large = weak pro-start (scanner/source change)
  phash     64-bit dHash Hamming distance between renders: low = anti-start, high = pro-start

Usage:
  python -u src/signals.py eval "Case 3" ["Case 1" ...]   # score signals vs gold + saved preds
Evaluation targets the two production roles:
  SPLIT ranking  - among the LLM's predicted starts, do anti-start signals concentrate on the
                   FALSE ones? (feeds the split-verification pass)
  MERGE detection- among pages INSIDE predicted spans, do pro-start signals hit the known
                   missed gold starts, and at what false-alarm cost? (worst-class detector)
"""

import io
import json
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import images
from config import OUTPUTS
from diagnose_naive import resolve
from pypdf import PdfReader, PdfWriter
from run_phase0 import _case

try:
    from PIL import Image
except ImportError as exc:  # Pillow is an app dependency; fail loud if the venv changed
    raise SystemExit(f"Pillow required for the phash signal: {exc}") from exc

CACHE_DIR = os.path.join(OUTPUTS, "signals-cache")

_PAGENUM = re.compile(r"page\s{0,2}(\d{1,3})\s{0,2}(?:of|/)\s{0,2}(\d{1,4})", re.IGNORECASE)
_BANNERISH = re.compile(
    r"fax|facsimile|transmi|(\d{1,2}/\d{1,2}/\d{2,4}\D{0,10}\d{1,2}:\d{2})", re.IGNORECASE
)
_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")


def _top_lines(text, k=3):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[:k]


def _normalize(s):
    return re.sub(r"[\d\W]+", " ", (s or "").lower()).strip()


def page_features(pages_text):
    """Per-page cheap features from OCR text (1-indexed list access via [p-1])."""
    feats = []
    for text in pages_text:
        top = _top_lines(text)
        pagenum = None
        m = _PAGENUM.search((text or "")[:2000])
        if m and int(m.group(2)) >= int(m.group(1)) >= 1:
            pagenum = (int(m.group(1)), int(m.group(2)))
        banner = next((_normalize(ln) for ln in top if _BANNERISH.search(ln)), None)
        feats.append(
            dict(
                pagenum=pagenum,
                banner=banner,
                header=_normalize(" ".join(top)),
                dates={m.group(0) for m in _DATE.finditer(text or "")},
            )
        )
    return feats


def _dhash(png_bytes):
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def _hamming(a, b):
    return (a ^ b).bit_count()


def page_hashes(alias, pdf_path, n, dpi=60):
    """dHash per page, cached to disk (rendering 100s of pages is the slow part)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"{alias}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            stored = json.load(f)
        if len(stored) == n:
            return stored
    hashes = []
    for p in range(1, n + 1):
        hashes.append(_dhash(images.render_png(pdf_path, p, dpi)))
        if p % 50 == 0:
            print(f"  [phash] {p}/{n} pages hashed", flush=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(hashes, f)
    return hashes


def page_sizes(pdf_path, n):
    reader = PdfReader(pdf_path)
    sizes = []
    for p in range(n):
        w = PdfWriter()
        w.add_page(reader.pages[p])
        buf = io.BytesIO()
        w.write(buf)
        sizes.append(len(buf.getvalue()))
    return sizes


def transition_signals(feats, hashes, sizes, p):
    """Signal readout for the transition INTO page p (does a new doc start at p?).
    Returns dict of signal -> value in [-1, 1]: positive = pro-start, negative = anti-start,
    0 = no evidence. Thresholds are first-cut; the eval decides which signals survive."""
    cur, prev = feats[p - 1], feats[p - 2]
    out = {}

    pn = cur["pagenum"]
    out["pagenum"] = 0
    if pn:
        out["pagenum"] = 1 if pn[0] == 1 else -1

    out["banner"] = 0
    if cur["banner"] and prev["banner"]:
        same = SequenceMatcher(None, cur["banner"], prev["banner"]).ratio() >= 0.7
        out["banner"] = -1 if same else 1

    sim = SequenceMatcher(None, cur["header"], prev["header"]).ratio() if cur["header"] and prev["header"] else None
    out["header"] = 0 if sim is None else (-1 if sim >= 0.75 else (1 if sim <= 0.35 else 0))

    shared = cur["dates"] & prev["dates"]
    out["dates"] = 0
    if shared:
        out["dates"] = -1
    elif cur["dates"] and prev["dates"]:
        out["dates"] = 1

    rel = abs(sizes[p - 1] - sizes[p - 2]) / max(sizes[p - 1], sizes[p - 2], 1)
    out["bytes"] = 1 if rel >= 0.5 else 0

    d = _hamming(hashes[p - 1], hashes[p - 2])
    out["phash"] = -1 if d <= 8 else (1 if d >= 24 else 0)
    return out


def _stats(flagged, positives, universe):
    """precision/recall of a flag set against a positive set within a universe."""
    tp = len(flagged & positives)
    prec = tp / len(flagged) if flagged else None
    rec = tp / len(positives) if positives else None
    return tp, len(flagged), len(positives), prec, rec, len(universe)


def eval_case(alias):
    cid, alias = resolve(alias)
    c = _case(cid)
    n = c["n"]
    with open(
        os.path.join(OUTPUTS, "naive-diagnosis", alias, "pred_rows.json"), encoding="utf-8"
    ) as f:
        data = json.load(f)
    rows = sorted(data["rows"], key=lambda r: r["s"])
    seams = {tuple(w)[0] for w in data["windows"][1:]}
    pred_starts = sorted({r["s"] for r in rows} - {1} - seams)
    gold = set(c["gold_starts"])
    near_gold = {g + d for g in gold for d in (-1, 0, 1)}

    feats = page_features(c["pages_text"])
    hashes = page_hashes(alias, c["pdf"], n)
    sizes = page_sizes(c["pdf"], n)
    sig = {p: transition_signals(feats, hashes, sizes, p) for p in range(2, n + 1)}
    names = list(next(iter(sig.values())).keys())

    print(f"\n=== {alias}: {n}pp, {len(gold)} gold, {len(pred_starts)} predicted starts "
          f"(non-seam), evaluating {names}")

    # A. SPLIT ranking: anti-start evidence should concentrate on FALSE predicted starts.
    false_pred = {p for p in pred_starts if p not in near_gold}
    print(f"[split targeting] false predicted starts (+-1 tolerant): {len(false_pred)} "
          f"of {len(pred_starts)}")
    for name in names:
        flagged = {p for p in pred_starts if sig[p][name] < 0}
        tp, nf, npos, prec, rec, _ = _stats(flagged, false_pred, set(pred_starts))
        print(f"  {name:8} anti-start flags {nf:>3} pred starts; {tp:>3} are false "
              f"(precision {prec:.2f}, catches {rec:.0%} of false starts)"
              if nf and npos else f"  {name:8} anti-start flags {nf:>3} (no basis)")

    # B. MERGE detection: pro-start evidence INSIDE predicted spans should hit missed gold starts.
    interior = [p for p in range(2, n + 1) if p not in set(pred_starts) and p not in seams]
    missed = {g for g in gold if g in set(interior)}
    print(f"[merge detection] interior transitions: {len(interior)}; "
          f"gold starts hidden inside spans: {len(missed)} {sorted(missed)}")
    for name in names:
        flagged = {p for p in interior if sig[p][name] > 0}
        tp, nf, npos, prec, rec, _ = _stats(flagged, missed, set(interior))
        rate = nf / max(len(interior), 1) * 100
        hit = f"hits {tp}/{npos}" if npos else "n/a (no misses here)"
        print(f"  {name:8} pro-start flags {nf:>3} interior pages ({rate:.0f}% of interior); "
              f"{hit}")
    return dict(alias=alias, sig=sig)


if __name__ == "__main__":
    if sys.argv[1:2] != ["eval"]:
        raise SystemExit('usage: python -u src/signals.py eval "Case 3" ["Case 1" ...]')
    for alias in sys.argv[2:] or ["Case 3"]:
        eval_case(alias)
