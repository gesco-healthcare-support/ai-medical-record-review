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
            "manualCheck": False,
            "summaryText": "Some **bold** body text for the first record.",
            "startPage": 2,
        },
        {
            "summaryDate": "02/01/2020",
            "linkTitle": "OPERATIVE REPORT (Pages 3-3)",
            "manualCheck": True,
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
    # One link per entry (proves the blue-title-span detection produced real hotspots).
    assert len(gotos) == 2
    for pno, link in gotos:
        assert pno < summ  # hotspot sits on a summary page
        assert link["from"].width > 1 and link["from"].height > 1  # clickable, non-zero area
    targets = sorted(link["page"] for _, link in gotos)
    assert targets == [summ + 1, summ + 2]  # startPage 2 and 3 -> combined source indices
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
