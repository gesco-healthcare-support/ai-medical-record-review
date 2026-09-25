"""Unit tests for the native linked-PDF builder (no DB, no network)."""

import re

import pymupdf

from app.services.linked_pdf import build_linked_pdf
from app.services.reporting import ReportDetails


def _make_source(tmp_path, pages: int) -> str:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    path = tmp_path / "src.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_build_linked_pdf_structure_and_links(tmp_path):
    source = _make_source(tmp_path, pages=3)
    entries = [
        {
            "summaryDate": "01/01/2020",
            "linkTitle": "PROGRESS REPORT (Pages 2-2)",
            "summaryText": "Some **bold** body text for the first record.",
            "startPage": 2,
        },
        {
            "summaryDate": "02/01/2020",
            "linkTitle": "OPERATIVE REPORT (Pages 3-3)",
            "summaryText": "More body text for the second record.",
            "startPage": 3,
        },
    ]
    data = build_linked_pdf(
        source,
        entries,
        num_pages=3,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1990",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )

    doc = pymupdf.open(stream=data, filetype="pdf")
    src_pages = 3
    summ = doc.page_count - src_pages
    assert summ >= 1  # summary letter precedes the source

    gotos = [
        (pno, link)
        for pno in range(doc.page_count)
        for link in doc[pno].get_links()
        if link.get("kind") == pymupdf.LINK_GOTO
    ]
    # One link per entry, each a real (non-zero-area) hotspot on a summary page.
    assert len(gotos) == 2
    for pno, link in gotos:
        assert pno < summ  # hotspot sits on a summary page
        # clickable, non-zero area
        assert link["from"].width > 1
        assert link["from"].height > 1
    targets = sorted(link["page"] for _, link in gotos)
    assert targets == [summ + 1, summ + 2]  # startPage 2 and 3 -> combined source indices
    doc.close()


def test_build_linked_pdf_links_every_title_in_a_multipage_letter(tmp_path):
    """Regression: a large letter spans several pages; EVERY title must still link to its correct
    source page. The old blue-span pairing dropped ~30% of links on big real docs (48/68)."""
    n = 30
    source = _make_source(tmp_path, pages=n + 5)
    entries = []
    for i in range(n):
        start = i + 1
        long = " ".join(["Comprehensive"] * (1 + (i % 6)))  # vary length so some titles wrap
        entries.append(
            {
                "summaryDate": f"{(i % 12) + 1:02d}/01/2020",
                "linkTitle": f"{long} Report {i} (Pages {start}-{start})",
                "summaryText": f"Body text for record number {i}. " * 3,
                "startPage": start,
            }
        )
    data = build_linked_pdf(
        source,
        entries,
        num_pages=n + 5,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1990",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )
    doc = pymupdf.open(stream=data, filetype="pdf")
    summ = doc.page_count - (n + 5)
    assert summ >= 2  # the letter genuinely spans multiple pages

    targets = [
        link["page"]
        for pno in range(summ)
        for link in doc[pno].get_links()
        if link.get("kind") == pymupdf.LINK_GOTO
    ]
    expected = {summ + (e["startPage"] - 1) for e in entries}
    assert expected.issubset(set(targets))  # every title's source page is linked
    assert len(set(targets)) == n  # all 30 distinct titles linked (none dropped)
    doc.close()


def test_an_entry_longer_than_a_page_is_delivered_whole(tmp_path):
    """WHEN one entry's body is taller than a page, THE SYSTEM SHALL render all of it.

    A reviewer reported entries "cut off if they don't fit entirely on one page", with the Word
    document carrying the whole entry - so the text existed and only the PDF lost it. The cause is
    that Story cannot split a TABLE ROW across a page break, and each entry was one row: measured on
    that shape, a 90-sentence body kept 72 of 90 sentences and a 120-sentence body kept NONE while
    `place` never reported itself finished.

    The last sentence is the assertion that matters - a clipped entry keeps its opening, so checking
    that the body "appears" would pass on the very bug this pins.
    """
    source = _make_source(tmp_path, pages=3)
    body = " ".join(f"Sentence {i} of a very long clinical narrative." for i in range(1, 121))
    data = build_linked_pdf(
        source,
        [
            {
                "summaryDate": "01/14/2026",
                "linkTitle": "Long Evaluation (Pages 1-1)",
                "summaryText": body,
                "startPage": 1,
            }
        ],
        num_pages=3,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1990",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = [doc[p].get_text() for p in range(doc.page_count - 3)]
    doc.close()
    # Whitespace-collapsed, because a wrapped line puts a newline INSIDE a sentence and the search
    # would then miss text that is plainly on the page - every 7th sentence, on the first run.
    letter = re.sub(r"\s+", " ", " ".join(pages))

    assert "Sentence 1 of" in letter
    assert "Sentence 120 of" in letter
    missing = [i for i in range(1, 121) if f"Sentence {i} of" not in letter]
    assert not missing, f"{len(missing)} sentence(s) lost, first {missing[:5]}"


def test_a_long_entry_still_links_to_its_source_page(tmp_path):
    """The link rects come from Story's `element_positions`, so a change to how an entry is laid
    out could deliver the whole body and silently stop linking it."""
    source = _make_source(tmp_path, pages=3)
    body = " ".join(f"Sentence {i} of a very long clinical narrative." for i in range(1, 121))
    data = build_linked_pdf(
        source,
        [
            {
                "summaryDate": "01/14/2026",
                "linkTitle": "Long Evaluation (Pages 2-2)",
                "summaryText": body,
                "startPage": 2,
            }
        ],
        num_pages=3,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1990",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )
    doc = pymupdf.open(stream=data, filetype="pdf")
    summ = doc.page_count - 3
    targets = [
        link["page"]
        for pno in range(summ)
        for link in doc[pno].get_links()
        if link.get("kind") == pymupdf.LINK_GOTO
    ]
    doc.close()
    assert summ + 1 in targets  # source page 2, offset past the letter


def test_build_linked_pdf_empty_entries_is_summary_only(tmp_path):
    source = _make_source(tmp_path, pages=2)
    data = build_linked_pdf(
        source,
        [],
        num_pages=2,
        patient_name="P",
        patient_dob="-",
        qme_or_ame="",
        details=ReportDetails(lawfirm="Firm"),
    )
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert doc.page_count >= 2  # letter (>=1 page) + 2 source pages
    gotos = [
        link
        for pno in range(doc.page_count)
        for link in doc[pno].get_links()
        if link.get("kind") == pymupdf.LINK_GOTO
    ]
    assert gotos == []
    doc.close()


def test_the_running_header_carries_the_patient_name_and_dob(tmp_path):
    """WHEN a linked PDF is built, THE SYSTEM SHALL print the patient identity on the summary pages.

    The summary letter's HTML never receives the identity: `_draw_running_header` paints it onto the
    rendered pages instead. Pinned here so that removing the unused parameters from the HTML builder
    cannot quietly drop the identity from the deliverable - nothing else asserted it.
    """
    source = _make_source(tmp_path, pages=2)
    data = build_linked_pdf(
        source,
        [
            {
                "summaryDate": "01/01/2020",
                "linkTitle": "PROGRESS REPORT (Pages 1-1)",
                "summaryText": "Body text.",
                "startPage": 1,
            }
        ],
        num_pages=2,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1990",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )
    doc = pymupdf.open(stream=data, filetype="pdf")
    summary_text = doc[0].get_text()
    doc.close()
    assert "Synthetic Patient" in summary_text
    assert "01/01/1990" in summary_text


# A deposition body is one paragraph per page group, and the linked PDF used to print it as ONE
# block: the body goes into HTML, where a newline is only whitespace. The senior reviewer read a
# whole transcript summary that way and asked for it "broken up into multiple paragraphs"
# (2026-09-25).
_DEPO = "On pages 1 to 10, asked a question.\nOn pages 11 to 20, stated an answer."


def _line_starts(data) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    starts = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if text:
                    starts.append(text)
    return starts


def test_each_deposition_paragraph_starts_its_own_line(tmp_path):
    """DEMONSTRATES the fix: the second page group opens a line of its own, not mid-sentence."""
    source = _make_source(tmp_path, pages=2)
    entries = [
        {
            "summaryDate": "01/01/2020",
            "linkTitle": "DEPOSITION OF A WITNESS",
            "summaryText": _DEPO,
            "startPage": 1,
        },
    ]
    data = build_linked_pdf(
        source,
        entries,
        num_pages=2,
        patient_name="Synthetic Patient",
        patient_dob="-",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Firm"),
    )
    starts = _line_starts(data)
    assert any(s.startswith("On pages 11 to 20") for s in starts)
    assert not any("asked a question. On pages 11" in s for s in starts)
