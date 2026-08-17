"""Gemini segmentation assets: prompt, response schema, and tolerant row parsing.

PDF delivery is INLINE (see services/segment_engine.py): the Files API existed only on
the non-BAA Developer endpoint and was removed with the Vertex port.
"""

# Historical provenance stamp. DO NOT hand-bump this any more: `jobs.prompt_fingerprint` and
# `summaries.prompt_fingerprint` are now COMPUTED from the prompt text as resolved (see
# services/provenance.py), so provenance moves on its own.
#
# The old instruction here was "bump on ANY change to the prompts or schema below". That is exactly
# what failed: it went unbumped through roughly a dozen prompt PRs, so every job from all of them
# carries the same value and stored rows became untraceable to the prompt that made them. An
# instruction that nothing verifies is an instruction that gets skipped, so it is removed rather
# than restated more loudly.
#
# The constant stays because rows written before 2026-08-06 have nothing else, and it is still
# written to every Job. "1" = original; "2" = 2026-07-06 recall-first rework; "3" = 2026-08-03,
# injury date also read from a "Date of Onset" field.
#
# NOT bumped for the 2026-08-06 removal of the injury-date field from the prompt and schema,
# deliberately: job_prompt_fingerprint(session, "segment") hashes SEGMENTATION_SYSTEM +
# SEGMENTATION_PROMPT, so that edit moves the computed stamp on its own. Bumping by hand as well
# would reintroduce the habit this comment exists to end.
PROMPT_VERSION = "3"

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
"Page N" means the N-th page of THIS file, counting from 1. Count positions yourself from the first page you were given: printed page numbers identify nothing here, because scanned bundles restart and repeat their printed numbering.

## Coverage - the output is used to slice the file, so it must tile it exactly
- Records appear in page order, every page belongs to exactly one record, and consecutive records touch: each record starts on the page immediately after the previous record ends. Together they run from page 1 through the last page.
- Each record covers ONE contiguous run of pages. When one document's pages appear in two separate places in the file, report each run as its own record, because a single record cannot express a gap.
- When the file starts or ends mid-document, report that partial document with the page range visible here.
- Attach each blank page to the record that PRECEDES it, and any blank page before the first document to the first document: scanners emit blank backsides and separators, so a blank page belongs to its neighbour.
- Assign pages you cannot read to the record they physically sit within. Every page reaches some record, so an unreadable page is placed rather than skipped.

## Where a sub-document starts
- At its first physical page, INCLUDING any fax cover sheet, transmittal letter, or routing slip that travels with it: a cover belongs to the document it introduces, so the record begins ON the cover page.
- Strong start signals: a new letterhead or form header together with a new document title; the first page of a form; a new author within a run of same-type documents.
- These pages CONTINUE the record already open: a page marked "page N of M"; lab tables, signature pages, and attachments belonging to the report they follow; a letterhead that changes INSIDE one report. Long medico-legal evaluations (QME/PQME/AME) quote many other records - keep the entire evaluation as ONE record. A distinct QME/AME supplemental report is its own record.
- A report often EMBEDS a few pages that look like a different document type (lab tables, an imaging summary, a work-status form, a copied letter). When those pages carry the report's date or are referenced by the surrounding text, extend the report's range OVER them.
- A document's FIRST or LAST pages often look unlike its body: certification or notary stamps, letterhead-only or branding pages, terms-and-conditions or disclaimer pages, distribution/cc lists. Include these in the range of the document they accompany.
- The ENCOUNTER date is the strongest boundary evidence these files carry: two documents almost never report the same encounter, so when a page states a DIFFERENT encounter date from the page before it, a new document has begun.
- Use the encounter date as field "d" below defines it - the date of the visit or service the document DESCRIBES. Fax, print, received, transcription and re-send stamps are NOT encounter dates, and in a scanned bundle those change from page to page WITHIN a single document, so reading them as boundaries tears one document into many. Take the date from the document's own header or from beside the signature.
- A change of TITLE on its own does NOT open a new document. Headings move around inside one document, because a lab table, a work-status form, or a letter copied into a report each carry their own. Use the title to CORROBORATE a date change; only where NEITHER page states an encounter date may a clearly different document title stand in for one.
- Consecutive visits of the same type (physiotherapy, chiropractic, acupuncture) carry near-identical titles, so the DATE is what separates them - one record per encounter date.
- When the encounter date AND the title are both the same across consecutive pages, those pages usually belong to ONE document. Same-day batches are the exception, and they announce themselves: a fresh letterhead, a restarted "Page 1 of N", or a separate signature block marks each new item (one per visit, one per body part, one per form).
- Default when a page is hard to place: it CONTINUES the record already open, unless you can name a specific start signal visible on it - one of the strong signals above, or a new encounter date. Name that signal before you split; when you cannot name one, the page continues.
- One nameable start signal is enough to split. Weigh the two mistakes unequally: a false split costs a reviewer one merge click, while a document buried inside another record is never seen again. So do not withhold a split that has evidence behind it - the bar is visible evidence on the page, not how confident you feel.

## Fields (use "-" whenever a value is unavailable; never null)
- "t" title: the document's own title or header wording if visible (it may sit next to a label such as "Notes"); otherwise name the document type in plain words, for example "Progress Note", "MRI Report", or "Work Status Report". Replace any comma with a dash so the value stays CSV-safe. A title of the form "X vs Y" is almost always a deposition: use "Deposition".
- "d" document date: the visit/encounter date of THIS document as MM/DD/YYYY (it may sit near the signature at the end). When the page carries several dates, report the date of the encounter the document DESCRIBES rather than the date it was written, signed, transcribed, faxed, printed, or re-sent. A date of injury is not a document date: leave it out of this field. When the document states no encounter date, use "-".
- "m" manual check: "x" if a human should review the document - substantial handwriting (more than a signature), checkbox-style forms, work-status reports, or QME/PQME/AME reports; otherwise "-".

Example output for a 10-page file (format reference):
[
  {"id": "Doc1", "s": 1, "e": 5, "t": "WORK ACTIVITY STATUS", "d": "12/03/2021", "m": "x"},
  {"id": "Doc2", "s": 6, "e": 10, "t": "ACUPUNCTURE THERAPY NOTES", "d": "11/11/2022", "m": "-"}
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
            "m": {
                "type": "STRING",
                "enum": ["x", "-"],
                "description": "'x' when the document needs human review",
            },
        },
        "required": ["s", "e", "t", "d", "m"],
        "propertyOrdering": ["id", "s", "e", "t", "d", "m"],
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
        str(item.get("m", "-")).strip(),
    )
