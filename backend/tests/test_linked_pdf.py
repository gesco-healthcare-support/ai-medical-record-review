"""Unit tests for the native linked-PDF builder (no DB, no network)."""

import pymupdf

from app.services.linked_pdf import build_linked_pdf


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
        lawfirm="Example Firm",
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
        lawfirm="Example Firm",
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


def test_build_linked_pdf_empty_entries_is_summary_only(tmp_path):
    source = _make_source(tmp_path, pages=2)
    data = build_linked_pdf(
        source, [], num_pages=2, patient_name="P", patient_dob="-", qme_or_ame="", lawfirm="Firm"
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
