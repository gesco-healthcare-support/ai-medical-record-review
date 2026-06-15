"""Gemini segmentation: file upload, readiness polling, prompt, and response parsing."""

import time

from mrr_ai.extensions import genai_client


def upload_to_gemini(path, mime_type=None):
    """Uploads the given file to Gemini.
    See https://ai.google.dev/gemini-api/docs/prompting_with_media
    """
    file = genai_client.files.upload(file=path)
    print()
    print(f"Uploaded file '{file.display_name}' as: {file.uri}")
    return file


def wait_for_files_active(files):
    """Waits for the given files to be active.

    Some files uploaded to the Gemini API need to be processed before they can be
    used as prompt inputs. The status can be seen by querying the file's "state"
    field.

    This implementation uses a simple blocking polling loop. Production code
    should probably employ a more sophisticated approach.
    """
    print("Waiting for file processing...")
    for name in (file.name for file in files):
        file = genai_client.files.get(name=name)
        while file.state.name == "PROCESSING":
            print(".", end="", flush=True)
            time.sleep(3)
            file = genai_client.files.get(name=name)
        if file.state.name != "ACTIVE":
            raise Exception(f"File {file.name} failed to process")
    print("...all files ready")
    print()


SEGMENTATION_PROMPT = """Title: Extract Subdocument Metadata from a PDF

I have a large PDF document containing multiple subdocuments, each of which can vary in type (e.g., diagnostic reports, doctor's notes, legal forms, etc.).
Your task is to analyze the PDF and return a structured JSON array containing key metadata for each subdocument. Use EXACTLY these keys for every element:

1) "id" (subdocument ID): A unique identifier for each subdocument (e.g., Doc1, Doc2, etc.).
2) "s" (start page): The page number where the subdocument begins (integer).
3) "e" (end page): The page number where the subdocument ends (integer).
4) "t" (title): The title or header of the subdocument, if available. Do not invent titles; if needed, infer from the document type. DO NOT use commas; convert any comma to a dash (-). For example: WORK ACTIVITY STATUS
5) "d" (date of the document/encounter): The visit/encounter date as MM/DD/YYYY, else "-". If there are several dates, pick the one labeled visit or encounter. The date can be near the signature at the end.
6) "i" (date of injury): The injury date as MM/DD/YYYY, else "-".
7) "m" (manual check): Return "x" if the document (1) has handwriting other than a signature, (2) has many checkboxes with x/ticks, (3) is a work status report, or (4) is a QME/AME report; otherwise "-".

## Guidelines for Extraction:
- Cover every page; do not skip any page.
- Use contextual clues such as headers, bold titles, or consistent formatting to identify boundaries and titles.
- Link pages together using page counts to figure out where documents start and end.
- Distinguish the document/encounter date from the injury date.
- A title can sometimes be next to a word such as 'Notes'.
- If a field is unavailable, use "-" (never None or null).
- Ignore fax/resend dates; use the encounter/visit date or the day the document was created.
- If a title contains "X vs Y", it is most likely a deposition; set "t" to "Deposition".
- If the only handwriting is a signature, return "-" not "x".
- If a page is empty, set "t" to "Empty Page".
- Do not split a single document into two, and do not merge two documents into one.
- QME/PQME/AME evaluations can be long and often quote other records; treat the entire evaluation as ONE record with the correct start and end pages.
- Different QME/PQME/AME supplemental reports are separate documents.
- Treat the first page of a document as part of that document.

Example JSON output for a 10-page PDF:
[
  {"id": "Doc1", "s": 1, "e": 5, "t": "WORK ACTIVITY STATUS", "d": "12/03/2021", "i": "05/07/2018", "m": "x"},
  {"id": "Doc2", "s": 6, "e": 10, "t": "ACUPUNCTURE THERAPY NOTES", "d": "11/11/2022", "i": "-", "m": "-"}
]

## IMPORTANT: Return ONLY the JSON array, with no markdown fences and no other explanation."""


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
