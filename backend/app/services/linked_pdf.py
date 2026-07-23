"""Linked combined-PDF export: the MRR summary letter followed by the full source record.

`build_linked_pdf` renders the summary letter natively with PyMuPDF (a borderless two-column
table: date | linked-title + body), appends the entire uploaded source PDF, and adds an internal
GOTO link from each summary's title to that sub-document's first source page.

Why native PyMuPDF (not a docx->PDF conversion): it reproduced the reference layout most
faithfully, needs no extra dependency, and lets us place links deterministically. The link
hotspot is found by detecting the blue title spans on the rendered page -- Story reports inline
anchor positions as zero-width points, so those cannot be used for hotspots.
"""

import html
import io
import re
from datetime import datetime

import pymupdf

# The title link colour (#0000EE). Rendered from CSS and detected back from the page text so the
# clickable hotspot exactly covers the (possibly multi-line) title and nothing else.
LINK_BLUE = 0x0000EE

_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_", re.DOTALL)
_LETTER = pymupdf.paper_rect("letter")  # 612 x 792 pt


def _inline_html(text: str) -> str:
    """Escape ``text`` then turn **bold** / *italic* / _italic_ markers into <b>/<i>."""
    out, pos, esc = [], 0, html.escape(text or "")
    for m in _INLINE_RE.finditer(esc):
        if m.start() > pos:
            out.append(esc[pos : m.start()])
        out.append(
            f"<b>{m.group(1)}</b>"
            if m.group(1) is not None
            else f"<i>{m.group(2) or m.group(3)}</i>"
        )
        pos = m.end()
    out.append(esc[pos:])
    return "".join(out)


def _sort_key(entry: dict):
    try:
        return datetime.strptime(entry.get("summaryDate", ""), "%m/%d/%Y")
    except ValueError:
        return datetime.min  # undated entries sort first, matching build_mrr_document


def _norm(s: str) -> str:
    """Collapse whitespace for tolerant title matching across line/page wraps."""
    return " ".join((s or "").split())


def blue_title_groups(page) -> list[tuple[pymupdf.Rect, str]]:
    """Group the page's blue (link-coloured) text spans into per-title (rect, text) pairs.

    A group is a maximal run of consecutive spans whose fill colour is LINK_BLUE; the first
    non-blue span (e.g. the black ". " after the title, or the body) closes it. Consecutive blue
    spans across a line wrap union into one rectangle, so a multi-line title yields one hotspot.
    """
    groups: list[tuple[pymupdf.Rect, str]] = []
    cur: pymupdf.Rect | None = None
    cur_text: list[str] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("color") == LINK_BLUE:
                    rect = pymupdf.Rect(span["bbox"])
                    cur = rect if cur is None else (cur | rect)
                    cur_text.append(span.get("text", ""))
                elif cur is not None:
                    groups.append((cur, "".join(cur_text)))
                    cur, cur_text = None, []
    if cur is not None:
        groups.append((cur, "".join(cur_text)))
    return groups


def _summary_html(entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm) -> str:
    rows = []
    for e in entries:
        rows.append(
            f"<tr><td class='d'>{html.escape(e.get('summaryDate') or '')}</td>"
            f"<td class='b'><a class='ln'>{html.escape(e['linkTitle'])}</a>. "
            f"{_inline_html(e['summaryText'])}</td></tr>"
        )
    return f"""<html><head><style>
      body {{ font-family: 'Times New Roman', serif; font-size: 11pt; }}
      .ttl {{ text-align: center; font-weight: bold; text-decoration: underline; font-size: 12pt; margin: 10pt 0; }}
      .h2 {{ font-weight: bold; text-decoration: underline; font-size: 12pt; }}
      p {{ margin: 0 0 8pt 0; text-align: justify; }}
      table {{ width: 100%; border-collapse: collapse; }}
      td {{ vertical-align: top; padding: 0 0 10pt 0; }}
      td.d {{ width: 72px; }}
      td.b {{ text-align: justify; }}
      a.ln {{ color: #0000EE; text-decoration: underline; font-weight: bold; }}
    </style></head><body>
      <p class='ttl'>{html.escape(qme_or_ame or " ")}</p>
      <p class='h2'>MEDICAL RECORD REVIEW</p>
      <p>I have received {num_pages} pages of medical records from {html.escape(lawfirm)}. I have
      reviewed all of the pages received and my opinion is based upon such received records.</p>
      <p style='font-weight:bold;'>The following is a summary of those records:</p>
      <table>{"".join(rows)}</table>
      <p>This concludes the review of submitted records.</p>
    </body></html>"""


def _render_summary_pdf(html_doc: str):
    """Render the letter HTML into a standalone PDF (paginated) and return the open Document."""
    story = pymupdf.Story(html=html_doc)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    content = pymupdf.Rect(72, 90, _LETTER.width - 72, _LETTER.height - 72)
    more = 1
    while more:
        dev = writer.begin_page(_LETTER)
        more, _ = story.place(content)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open(stream=buf.getvalue(), filetype="pdf")


def _draw_running_header(summary_doc, patient_name, patient_dob):
    for i in range(summary_doc.page_count):
        text = f"RE: {patient_name}\nDOB: {patient_dob}" + ("" if i == 0 else f"\nPage {i + 1}")
        summary_doc[i].insert_textbox(
            pymupdf.Rect(72, 30, 400, 88), text, fontsize=10, fontname="tiro"
        )


def build_linked_pdf(
    source_path, entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm
) -> bytes:
    """Build the combined linked PDF as bytes.

    ``entries``: dicts of {summaryDate, linkTitle, summaryText, startPage}, where
    ``startPage`` is the sub-document's first page in the SOURCE (1-based). Entries are sorted
    chronologically here (mirrors build_mrr_document). The summary letter is placed first, then
    the full source; each title links to combined page ``summary_pages + startPage - 1``.
    """
    entries = sorted(entries, key=_sort_key)

    summary_doc = _render_summary_pdf(
        _summary_html(entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm)
    )
    _draw_running_header(summary_doc, patient_name, patient_dob)
    summ_n = summary_doc.page_count

    combined = pymupdf.open()
    combined.insert_pdf(summary_doc)  # summary letter first
    source_doc = pymupdf.open(source_path)
    combined.insert_pdf(source_doc)  # full source appended

    # Collect the blue title hotspots across the summary pages, then link each to its source page.
    groups = [
        (sp, rect, text) for sp in range(summ_n) for rect, text in blue_title_groups(combined[sp])
    ]
    if len(groups) == len(entries):
        pairs = [(groups[i][0], groups[i][1], entries[i]) for i in range(len(entries))]
    else:
        # A title split across a page break yields an extra group; match each group to the entry
        # whose title contains its text (order-independent, page-split safe).
        pairs = []
        for sp, rect, text in groups:
            norm = _norm(text)
            match = next((e for e in entries if norm and norm in _norm(e["linkTitle"])), None)
            if match is not None:
                pairs.append((sp, rect, match))

    for sp, rect, entry in pairs:
        combined[sp].insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "page": summ_n + (int(entry["startPage"]) - 1),
                "from": rect,
                "to": pymupdf.Point(0, 0),  # top of the target page (PyMuPDF top-left origin)
            }
        )

    result = combined.tobytes()
    combined.close()
    summary_doc.close()
    source_doc.close()
    return result
