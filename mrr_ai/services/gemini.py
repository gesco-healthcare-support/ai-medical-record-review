"""Gemini segmentation assets: prompt, response schema, and tolerant row parsing.

PDF delivery is INLINE (see services/segment_engine.py): the Files API existed only on
the non-BAA Developer endpoint and was removed with the Vertex port.
"""

SEGMENTATION_SYSTEM = (
    "You are an expert medical-records clerk. You split scanned workers' compensation "
    "medical-record files into their component documents and report exact page ranges "
    "and metadata."
)

SEGMENTATION_PROMPT = """The document above is one scanned medical-record file from a California workers' compensation case. It is a continuous excerpt of a larger record: it may begin in the middle of one document and end in the middle of another.

Split the file into its component sub-documents and return one JSON record per sub-document, in page order.

## What a sub-document is
One document produced by one author or facility for one encounter, report, or form - the unit a records reviewer would summarize as a single item (a progress report, an imaging report, a deposition, a claim form, one therapy visit note, etc.). Most sub-documents are SHORT - one to three pages is typical; multi-page spans are the exception (long reports, depositions, medico-legal evaluations), not the norm.

## Page numbers
"Page N" means the N-th page of THIS file, counting from 1. Ignore page numbers printed on the pages: scanned bundles restart and repeat their printed numbering, so printed numbers do not identify positions in this file.

## Coverage - the output is used to slice the file, so it must tile it exactly
- Every page belongs to exactly one sub-document: records must be in order, must not overlap, and must not leave gaps; together they cover page 1 through the last page.
- If the file starts or ends mid-document, still report that partial document with the page range visible here.
- Blank pages NEVER form their own record: scanners emit blank backsides and separators. Attach a blank page to the document BEFORE it; blank pages before the first document belong to the first document.

## Where a sub-document starts
- At its first physical page, INCLUDING any fax cover sheet, transmittal letter, or routing slip that travels with it. A cover page is never its own record, and a document never starts on the page after its cover.
- Strong start signals: a new letterhead or form header together with a new document title; the first page of a form; a new visit/encounter date or author within a run of same-type documents (consecutive progress notes from the same clinic are SEPARATE records, one per visit).
- NOT starts: "page N of M" continuation pages; lab tables, signature pages, or attachments that belong to the report they follow; a letterhead change INSIDE one report. Long medico-legal evaluations (QME/PQME/AME) quote many other records - keep the entire evaluation as ONE record. A distinct QME/AME supplemental report is its own record.
- A report often EMBEDS a few pages that look like a different document type (lab tables, an imaging summary, a work-status form, a copied letter). If those pages carry the report's date or are referenced by the surrounding text, they are part of the report - do not split them out as their own record.
- A document's FIRST or LAST pages often look unlike its body: certification or notary stamps, letterhead-only or branding pages, terms-and-conditions or disclaimer pages, distribution/cc lists. These belong to the document they accompany - never report them as separate records.
- Do NOT merge two records merely because they share a document type and date: these files routinely contain same-day batches of short same-type documents (one per visit, one per body part, one per form), and each is its own record.
- Tiebreak: when you are genuinely unsure whether a page starts a new document or continues the previous one, START A NEW RECORD. A reviewer merges a false split in one click, but a document hidden inside another record is never seen again.

## Fields (use "-" whenever a value is unavailable; never null)
- "t" title: the document's own title or header wording if visible (it may sit next to a label such as "Notes"); otherwise the document type. Replace any comma with a dash so the value stays CSV-safe. A title of the form "X vs Y" is almost always a deposition: use "Deposition".
- "d" document date: the visit/encounter date of THIS document as MM/DD/YYYY (it may be near the signature at the end); ignore fax, print, and re-send dates. Distinguish it from the injury date.
- "i" injury date: the date of injury as MM/DD/YYYY if stated.
- "m" manual check: "x" if a human should review the document - substantial handwriting (more than a signature), checkbox-style forms, work-status reports, or QME/PQME/AME reports; otherwise "-".

Example output for a 10-page file (format reference):
[
  {"id": "Doc1", "s": 1, "e": 5, "t": "WORK ACTIVITY STATUS", "d": "12/03/2021", "i": "05/07/2018", "m": "x"},
  {"id": "Doc2", "s": 6, "e": 10, "t": "ACUPUNCTURE THERAPY NOTES", "d": "11/11/2022", "i": "-", "m": "-"}
]

Return ONLY the JSON array."""


# Structured-output schema for SEGMENTATION_PROMPT. Enforcing the shape via response_schema
# (not prose) guarantees parseable, correctly-typed records per the Gemini structured-output
# guidance; field descriptions steer the model, and the app still validates values.
SEGMENT_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING", "description": "Sequential id: Doc1, Doc2, ..."},
            "s": {
                "type": "INTEGER",
                "description": "First page of the sub-document: 1-based position in THIS file",
            },
            "e": {"type": "INTEGER", "description": "Last page of the sub-document, inclusive"},
            "t": {"type": "STRING", "description": "Title or document type; no commas"},
            "d": {"type": "STRING", "description": "Visit/encounter date MM/DD/YYYY, or '-'"},
            "i": {"type": "STRING", "description": "Injury date MM/DD/YYYY, or '-'"},
            "m": {
                "type": "STRING",
                "enum": ["x", "-"],
                "description": "'x' when the document needs human review",
            },
        },
        "required": ["s", "e", "t", "d", "i", "m"],
        "propertyOrdering": ["id", "s", "e", "t", "d", "i", "m"],
    },
}
# NOTE: a self-reported per-row boundary-confidence enum was trialled here (2026-07-04) and
# removed: the model answered "high" on 231 of 232 rows across the two most error-dense cases,
# including every known near-miss. Boundary confidence must be COMPUTED (row tiling, cross-window
# disagreement), not asked of the model.


def parse_segment_item(item):
    """Tolerantly extract one subdocument record from a Gemini JSON element.

    Handles the t/title key alias, missing keys, and type coercion so a single
    malformed element raises (to be skipped by the caller) rather than the old
    behavior of a KeyError aborting the entire batch.
    """
    title = item.get("t") or item.get("title") or "-"
    if not isinstance(title, str):
        title = str(title)
    return (
        int(item["s"]),
        int(item["e"]),
        title.strip(),
        str(item.get("d", "-")).strip(),
        str(item.get("i", "-")).strip(),
        str(item.get("m", "-")).strip(),
    )
