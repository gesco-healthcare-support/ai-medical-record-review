"""Linked combined-PDF export: the MRR summary letter followed by the full source record.

`build_linked_pdf` renders the summary letter natively with PyMuPDF (a borderless two-column
table: date | linked-title + body), appends the entire uploaded source PDF, and adds an internal
GOTO link from each summary's title to that sub-document's first source page.

Link placement is deterministic. Each title is rendered with a unique element id (``t{index}``),
and PyMuPDF's Story reports where it laid that element out -- its rectangle(s) and page -- via
element_positions during rendering. We union those rectangles per title-and-page and drop the link
there. This needs no colour detection or title text matching, so every title on every page is
linked, including a title that wraps across a page break (it reports a rect on each page, and each
gets its own hotspot to the same target).

Why native PyMuPDF (not a docx->PDF conversion): it reproduced the reference layout most
faithfully and needs no extra dependency.
"""

import html
import io
import re
from datetime import datetime

import pymupdf

from app.services.reporting import (
    CONCLUSION,
    REVIEW_HEADING,
    SUMMARY_INTRO,
    TITLE_SEPARATOR,
    date_label,
    header_lines,
    intro_sentence,
    parsed_date,
)

_TITLE_COLOR = "#0000EE"  # link-blue for the clickable titles (CSS)
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_", re.DOTALL)
_LETTER = pymupdf.paper_rect("letter")  # 612 x 792 pt
_CONTENT = pymupdf.Rect(72, 90, _LETTER.width - 72, _LETTER.height - 72)


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
    """Undated LAST, matching build_mrr_document - see the note there. Both renderers share
    `parsed_date` so the two cannot drift: they produce the same deliverable in two formats and a
    reviewer compares them side by side."""
    return parsed_date(entry) or datetime.max


def _summary_html(entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm) -> str:
    rows = []
    for i, e in enumerate(entries):
        # id='t{i}' lets Story report this title's rendered rect (see _render_summary_pdf).
        rows.append(
            f"<tr><td class='d'>{html.escape(date_label(e))}</td>"
            f"<td class='b'><a class='ln' id='t{i}'>{html.escape(e['linkTitle'])}</a>"
            f"{html.escape(TITLE_SEPARATOR)}"
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
      a.ln {{ color: {_TITLE_COLOR}; text-decoration: underline; font-weight: bold; }}
    </style></head><body>
      <p class='ttl'>{html.escape(qme_or_ame or " ")}</p>
      <p class='h2'>{html.escape(REVIEW_HEADING)}</p>
      <p>{html.escape(intro_sentence(num_pages, lawfirm))}</p>
      <p style='font-weight:bold;'>{html.escape(SUMMARY_INTRO)}</p>
      <table>{"".join(rows)}</table>
      <p>{html.escape(CONCLUSION)}</p>
    </body></html>"""


def _render_summary_pdf(html_doc: str) -> tuple[pymupdf.Document, dict[int, list]]:
    """Render the letter HTML to a paginated PDF AND capture each title's rendered rect(s) + page.

    Returns (document, title_rects) where title_rects maps a title's index -> [(page, Rect), ...].
    Story's element_positions fires per positioned element during layout: for a title (id='t{i}')
    it emits a zero-width anchor point plus a real rectangle for each word, so we keep the non-empty
    rects and union them per (title, page) into one hotspot for that page.
    """
    story = pymupdf.Story(html=html_doc)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    state = {"page": 0}
    raw: dict[tuple, list] = {}  # (title_index, page) -> [Rect]

    def _record(elpos) -> None:
        eid = getattr(elpos, "id", None)
        if not eid or not eid.startswith("t"):
            return
        rect = pymupdf.Rect(elpos.rect)
        if rect.width > 0.5 and rect.height > 0.5:  # skip the zero-width inline anchor point
            raw.setdefault((int(eid[1:]), state["page"]), []).append(rect)

    more = 1
    while more:
        dev = writer.begin_page(_LETTER)
        more, _ = story.place(_CONTENT)
        story.element_positions(_record, {})
        story.draw(dev)
        writer.end_page()
        state["page"] += 1
    writer.close()

    title_rects: dict[int, list] = {}
    for (idx, page), rects in raw.items():
        union = rects[0]
        for r in rects[1:]:
            union |= r
        title_rects.setdefault(idx, []).append((page, union))
    return pymupdf.open(stream=buf.getvalue(), filetype="pdf"), title_rects


def _draw_running_header(summary_doc, patient_name, patient_dob):
    """The same two identifying lines the Word header carries, plus a page number from page 2.

    The lines come from `reporting.header_lines` rather than being written here, because they had
    already drifted: this renderer labelled the date of birth and the Word one did not, so the two
    deliverables named the patient differently on every page. Word gets the same shape through a
    separate first-page header and a PAGE field.
    """
    re_line, dob_line = header_lines(patient_name, patient_dob)
    for i in range(summary_doc.page_count):
        text = f"{re_line}\n{dob_line}" + ("" if i == 0 else f"\nPage {i + 1}")
        summary_doc[i].insert_textbox(
            pymupdf.Rect(72, 30, 400, 88), text, fontsize=10, fontname="tiro"
        )


def build_linked_pdf(
    source_path, entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm
) -> bytes:
    """Build the combined linked PDF as bytes.

    ``entries``: dicts of {summaryDate, linkTitle, summaryText, startPage}, where ``startPage`` is
    the sub-document's first page in the SOURCE (1-based). Entries are sorted chronologically here
    (mirrors build_mrr_document). The summary letter is placed first, then the full source; each
    title links to combined page ``summary_pages + startPage - 1``.
    """
    entries = sorted(entries, key=_sort_key)

    summary_doc, title_rects = _render_summary_pdf(
        _summary_html(entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm)
    )
    _draw_running_header(summary_doc, patient_name, patient_dob)
    summ_n = (
        summary_doc.page_count
    )  # summary pages come first, so their indices == combined indices

    combined = pymupdf.open()
    combined.insert_pdf(summary_doc)  # summary letter first
    source_doc = pymupdf.open(source_path)
    combined.insert_pdf(source_doc)  # full source appended

    # Link each title (by index) to its source page, at the rect(s) Story reported for it.
    for i, entry in enumerate(entries):
        target = summ_n + (int(entry["startPage"]) - 1)
        for page, rect in title_rects.get(i, []):
            combined[page].insert_link(
                {
                    "kind": pymupdf.LINK_GOTO,
                    "page": target,
                    "from": rect,
                    "to": pymupdf.Point(0, 0),  # top of the target page (PyMuPDF top-left origin)
                }
            )

    result = combined.tobytes()
    combined.close()
    summary_doc.close()
    source_doc.close()
    return result
