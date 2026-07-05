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

from mrr_ai.config import CLASSIFY_WORKERS, VERIFY_MODEL
from mrr_ai.extensions import genai_client
from mrr_ai.services.genai_retry import generate_with_retry

_VERIFY_SYSTEM = (
    "You review the segmentation of a scanned workers' compensation medical record. "
    "Given two adjacent segments with their metadata and page images, decide whether "
    "the second segment is part of the first document or a separate document."
)

# Rows this short next to another boundary are the measured scatter signature.
SHORT_ROW_PAGES = 2
# How many of the suspect fragment's own pages to show (fragments are 1-2 pages by
# selection; a small cap keeps the question easy - few pages, full information).
FRAGMENT_PAGE_CAP = 2


def _page_png(pdf_path, page, dpi=120):
    images = convert_from_path(pdf_path, first_page=page, last_page=page, dpi=dpi)
    buffer = io.BytesIO()
    images[0].save(buffer, format="PNG")
    return buffer.getvalue()


def _row_line(name, row):
    return (
        f"{name}: pages {row['start']}-{row['end']}, category {row['category']}, "
        f"date {row['date']}, title: {row.get('title') or '-'}"
    )


def _same_document(pdf_path, prev_row, row):
    """Rich yes/no check: is `row` a continuation of `prev_row`'s document?

    The model gets everything a human reviewer would use - both segments' metadata as
    text, the LAST page of document A, and the suspect fragment's own first pages
    (small by construction). Failure -> False (keep the boundary): merging on missing
    evidence would hide a document, the one error nobody downstream can detect.
    """
    fragment_end = min(row["end"], row["start"] + FRAGMENT_PAGE_CAP - 1)
    contents = [
        _row_line("Document A", prev_row)
        + "\n"
        + _row_line("Segment B", row)
        + "\n\n"
        + "The first image is the LAST page of document A. The following image(s) are "
        f"segment B (pages {row['start']}-{fragment_end}). Segment B may be the "
        "continuation of document A - remaining report pages, attachments, lab tables, "
        "signature/stamp/certification pages, terms or branding pages, or blank "
        "separator pages - OR it may begin a separate document.\n"
        "Answer YES if segment B belongs to document A; answer NO if it is a separate "
        "document.",
        types.Part.from_bytes(data=_page_png(pdf_path, prev_row["end"]), mime_type="image/png"),
    ]
    for page in range(row["start"], fragment_end + 1):
        contents.append(
            types.Part.from_bytes(data=_page_png(pdf_path, page), mime_type="image/png")
        )
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": ["YES", "NO"]},
        system_instruction=_VERIFY_SYSTEM,
    )
    try:
        response = generate_with_retry(
            genai_client, model=VERIFY_MODEL, contents=contents, config=config
        )
    except Exception as exc:
        print(f"verify oracle failed at page {row['start']}: {exc}")
        return False
    return (response.text or "").strip() == "YES"


def suspect_indices(rows):
    """Row indices worth one verification call: the later row of a same-category+
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
            suspects.append(i)
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

    suspects = suspect_indices(rows)
    report(0, len(suspects))
    same_doc = {}
    if suspects:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_same_document, pdf_path, rows[i - 1], rows[i]): i for i in suspects
            }
            for done, future in enumerate(as_completed(futures), start=1):
                same_doc[rows[futures[future]]["start"]] = future.result()
                report(done, len(suspects))

    out, affected = [], 0
    for row in rows:
        refuted = bool(out) and same_doc.get(row["start"]) is True
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
