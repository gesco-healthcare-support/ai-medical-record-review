"""Boundary verification merge pass: computed suspicion + a two-page check per suspect.

The segmenter's dominant residual error is near-boundary scatter: a second start lands
1-2 pages from a real boundary, creating small fragments the user must merge by hand
(294-page live test: 132 rows vs 67 in the human key, 28 of the extras being exactly
this). Prompting is measured-immune, so the fix is verification: suspicion is COMPUTED
from the rows themselves (the model's self-reported confidence was measured useless),
and each suspect boundary gets one cheap two-image question - "does a new document
start here?". SAME merges the row into its predecessor.

Measured on the labeled cases (2026-07-04/05 experiment runs): zero true boundaries
harmed, false splits removed, ~$0.01/case. An unverifiable suspect KEEPS its boundary -
a wrong merge hides a document (the worst error class), a wrong split is a one-click
human fix.
"""

import io
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import types
from pdf2image import convert_from_path

from mrr_ai.config import CLASSIFY_WORKERS, GENAI_MODEL
from mrr_ai.extensions import genai_client
from mrr_ai.services.genai_retry import generate_with_retry

_ADJACENT_SYSTEM = (
    "You segment a scanned medical-record PDF into its sub-documents. You are shown two "
    "consecutive pages. Decide whether the SECOND (current) page begins a NEW document "
    "or continues the SAME document as the first page."
)

# Rows this short next to another boundary are the measured scatter signature.
SHORT_ROW_PAGES = 2


def _page_png(pdf_path, page, dpi=120):
    images = convert_from_path(pdf_path, first_page=page, last_page=page, dpi=dpi)
    buffer = io.BytesIO()
    images[0].save(buffer, format="PNG")
    return buffer.getvalue()


def _adjacent_says_new(pdf_path, page):
    """One two-image check: does a new document start at `page`? Failure -> True (keep
    the boundary): merging on missing evidence would hide a document."""
    contents = [
        "First image = previous page. Second image = current page. Does the current "
        "page start a NEW document or continue the SAME one?",
        types.Part.from_bytes(data=_page_png(pdf_path, page - 1), mime_type="image/png"),
        types.Part.from_bytes(data=_page_png(pdf_path, page), mime_type="image/png"),
    ]
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": ["NEW", "SAME"]},
        system_instruction=_ADJACENT_SYSTEM,
    )
    try:
        response = generate_with_retry(
            genai_client, model=GENAI_MODEL, contents=contents, config=config
        )
    except Exception as exc:
        print(f"verify oracle failed at page {page}: {exc}")
        return True
    return (response.text or "").strip() != "SAME"


def suspect_starts(rows):
    """Boundary pages worth one verification call: the later row of a same-category+
    same-date pair (the model contradicting itself), and any short-fragment row."""
    suspects = []
    for i in range(1, len(rows)):
        row, prev = rows[i], rows[i - 1]
        same_cat_date = (
            row["category"] == prev["category"]
            and row["date"] == prev["date"]
            and row["date"] not in ("", "-")
        )
        short = (row["end"] - row["start"] + 1) <= SHORT_ROW_PAGES
        if same_cat_date or short:
            suspects.append(row["start"])
    return suspects


def verify_and_merge(pdf_path, rows, progress=None, workers=CLASSIFY_WORKERS, auto=False):
    """Verify suspect boundaries; refuted ones become MERGE SUGGESTIONS by default.

    Verdicts are fetched in parallel (independent calls). With auto=False (production
    default) a refuted boundary marks its row ``suggest_merge`` for a one-click accept
    in the editor - measured at scale (294pp, 104 suspects, 2026-07-05) the oracle
    wrongly refuted 3 REAL boundaries (~3% false-SAME), and an automatic merge hides a
    document, the worst error class. auto=True keeps the destructive merge for
    experiments; merging preserves tiling. Returns (rows, stats).
    """

    def report(current, total):
        if progress is not None:
            progress("verifying", current, total)

    suspects = suspect_starts(rows)
    report(0, len(suspects))
    says_new = {}
    if suspects:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_adjacent_says_new, pdf_path, p): p for p in suspects}
            for i, future in enumerate(as_completed(futures), start=1):
                says_new[futures[future]] = future.result()
                report(i, len(suspects))

    out, affected = [], 0
    for row in rows:
        refuted = bool(out) and says_new.get(row["start"]) is False
        if refuted and auto:
            previous = out[-1]
            previous["end"] = row["end"]
            if str(row["flag"]).strip().lower() == "x":
                previous["flag"] = "x"
            affected += 1
            continue
        row = dict(row)
        if refuted:
            row["suggest_merge"] = True
            affected += 1
        out.append(row)
    key = "merged_away" if auto else "suggested"
    return out, {"suspects": len(suspects), key: affected}
