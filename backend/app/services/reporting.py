"""MRR Word-document assembly (ported from the Flask export blueprint; python-docx, Flask-free).

`build_mrr_document` is shared by the export + bundle-summarize routes: it sorts summary entries
chronologically and assembles the letterhead + intro + per-record body into a python-docx
Document. The classic CSV/on-disk export routes are dropped.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The three client-facing sentences of the letter live HERE ONLY, and both renderers import them:
# this module builds the Word document, `linked_pdf` builds the same letter as HTML for the combined
# PDF. They used to hold their own copy of each string, and the copies had already drifted - the
# double space in "pages  received" was in the Word copy alone, so the delivered .docx and the
# delivered .pdf disagreed on a sentence the client reads. Two copies of one sentence silently
# diverging is the mechanism, not the typo, so there is one home for the text and a test that pins
# both renderers to it.
SUMMARY_INTRO = "The following is a summary of those records:"
CONCLUSION = "This concludes the review of submitted records."

# Same reason as the three sentences above: both renderers held their own copy and the copies had
# ALREADY diverged. Checked against the eight human deliverables on disk, which are the standard these
# artifacts are supposed to match:
#
#   heading    "MEDICAL RECORD REVIEW" in 8 of 8 files. The Word renderer said "Medical Record
#              Review"; the PDF renderer already had it in caps.
#   separator  329 date-anchored entries across those 8 files, every one a PERIOD after the title
#              and not one colon. The Word renderer emitted ": "; the PDF renderer already used ". ".
#
# So on both counts the .docx disagreed with the .pdf AND with the human standard, and the .docx is
# the primary deliverable. Centralised here so the next change moves both renderers at once.
REVIEW_HEADING = "MEDICAL RECORD REVIEW"
TITLE_SEPARATOR = ". "
_REPORT_FONT = "Times New Roman"

# Each evaluator reads their reports in their own typeface, so the Word document is written
# in theirs. Supplied by the reviewers 2026-09-14, verbatim except that `Amasis MT Pro medium`
# is spelled with the capital the font itself carries.
#
# WORD ONLY, and that is the reviewers' own decision: python-docx writes the font NAME and the
# reader's Word resolves it, so nothing has to be installed here. The linked PDF is rendered
# by us and would need the real font files - five of these are licensed - and they answered
# that the PDF "does not need the font on that one, the rest of the files are for internal
# use". So `build_linked_pdf` deliberately does not take a doctor.
#
# The single source of truth for BOTH halves: the API serves `DOCTORS` to the dropdown and
# this module reads the font. Two lists would drift, which is the failure this repo keeps
# finding.
DOCTOR_FONTS: dict[str, str] = {
    "Falkinstein": "Times New Roman",
    "Pelton": "Tahoma",
    "Longacre": "Calibri",
    "Mikhael": "Century Gothic",
    "Hekmat": "Arial",
    "Andersen": "Georgia",
    "Nguyen": "Amasis MT Pro Medium",
    "Ziv": "Aptos Serif",
    "Grossman": "Aptos",
    "Perez": "Abadi",
    "Ahdoot": "Bierstadt Display",
}

DOCTORS: tuple[str, ...] = tuple(DOCTOR_FONTS)


def report_font(doctor: str | None) -> str:
    """The typeface for ``doctor``, or the house default for an unknown or absent one.

    Falls back rather than raising: the field is free-form on the way in, a doctor could be
    removed from the list while records still name them, and a delivered document in the
    wrong font is a smaller failure than an export that refuses."""
    return DOCTOR_FONTS.get((doctor or "").strip(), _REPORT_FONT)


# The covering letter that arrived WITH the records. `advocacy` is the initial request and
# `interrogatory` the supplemental one - the reviewers' own distinction, 2026-09-14. `none`
# is a real answer rather than a missing value: plenty of records arrive with no letter, and
# the opening paragraph then omits the clause entirely.
LETTER_TYPES: tuple[str, ...] = ("advocacy", "interrogatory", "none")

# The article travels WITH the label because it changes: `a defense advocacy letter` but
# `an interrogatory letter`. Computing it from the first letter would be a rule that happens
# to work on two values and breaks on the third someone adds.
LETTER_LABELS: dict[str, str] = {
    "advocacy": "a defense advocacy letter",
    "interrogatory": "an interrogatory letter",
}

# The disclosure the reviewers' own reports carry, verbatim from the format they sent. Fixed
# text: they were asked what goes in the credential slot and answered "Trained Medical Record
# Processor" - one string for everyone rather than a qualification per person.
#
# It names who did the record work, and it cites the statute that requires saying so. The
# reviewers flagged that this part may still need Patrick and Richard, so it is emitted ONLY
# when a reviewer name is supplied and is absent by default - a legal assertion with a blank
# name in it is not something to ship on a guess.
REVIEWER_CREDENTIAL = "Trained Medical Record Processor"

LABOR_CODE_CITE = "(California Labor Code \u00a7 4628(b)(c))"


@dataclass(frozen=True)
class ReportDetails:
    """The record-level facts the opening paragraph and the typeface need.

    One object rather than five more parameters because they travel together, arrive from one
    place - the header a reviewer fills in - and are read by one caller. Every field is
    optional and empty means absent, so the default instance renders the document exactly as
    it rendered before any of this existed."""

    doctor: str = ""
    attorney_name: str = ""
    # The firm rides here rather than staying a separate parameter, because `_sender_clause`
    # reads it and `attorney_name` as ONE fact - "from <person>, of <firm>" - and because both
    # builders are at Sonar's seven-parameter ceiling, which is what blocked the linked PDF from
    # taking `details` at all.
    lawfirm: str = ""
    # The page accounting rides here too, and for the same reason the firm does: S107 caps the
    # builder at seven parameters. It is the one field NOT typed by a reviewer - it is computed
    # from their rows - but it is a record-level fact the letter needs, which is what this is for.
    accounting: "RecordAccounting | None" = None
    letter_type: str = ""
    letter_date: str = ""
    reviewer_name: str = ""


def header_lines(patient_name, patient_dob) -> tuple[str, str]:
    """The two identifying lines both renderers put at the top of every page.

    Here for the same reason as the sentences above, and it had already gone the same way: the Word
    header wrote the date of birth as a bare value while the linked PDF labelled it ``DOB:``, so the
    two deliverables named the patient differently on every page. `RE:` was labelled in both, which
    is what makes the unlabelled one read as an omission rather than a house style.
    """
    return f"RE: {patient_name}", f"DOB: {patient_dob}"


def _sender_clause(attorney_name, lawfirm) -> str:
    """The sender: ` from <person>, of <firm>`, or whichever of the two is known, or nothing.

    Both are optional free text on the review page, and the clause is DROPPED rather than
    rendered empty: concatenating the firm unconditionally shipped "medical records from ."
    into a real export on 2026-08-17, dangling preposition and orphan full stop.

    `from <person>, of <firm>` only reads as intended with BOTH, so a person with no firm
    is named alone rather than shipped with a dangling "of"."""
    person = (attorney_name or "").strip()
    firm = (lawfirm or "").strip()
    if person and firm:
        return f" from {person}, of {firm}"
    if person or firm:
        return f" from {person or firm}"
    return ""


def _letter_clause(letter_type, letter_date) -> str:
    """The covering letter: `a defense advocacy letter dated 08/12/2026 along with `, or nothing.

    A letter with no date still gets its clause, because the TYPE is the fact worth stating
    and "dated" with nothing after it is worse than no date at all.

    A type with no label prints nothing at all. That covers `none`, which is a real answer
    rather than a missing value - it is how a reviewer says they checked and there was no
    letter, which is different from not having been asked."""
    label = LETTER_LABELS.get((letter_type or "").strip())
    if not label:
        return ""
    date = (letter_date or "").strip()
    return f"{label} dated {date} along with " if date else f"{label} along with "


def intro_sentence(
    num_pages,
    lawfirm,
    *,
    attorney_name="",
    letter_type="",
    letter_date="",
    reviewer_name="",
) -> str:
    """The letter's opening paragraph, shared by the Word and linked-PDF renderers.

    Follows the format the reviewers supplied on 2026-09-14, and every added element is
    CONDITIONAL - with no keyword arguments this returns what it always returned, so a record
    that predates the new header fields is unchanged.

    Each clause is dropped rather than rendered empty; `""`/whitespace/None all count as absent,
    which is how these arrive from the form.

    The two optional clauses are assembled by `_sender_clause` and `_letter_clause`, which
    carry the rules for dropping each one.

    The Labor Code sentences appear ONLY with a reviewer name. They are a legal assertion about
    who performed the record work, and emitting one with a blank name in it is not a guess to
    make - the reviewers flagged that this part may still need Patrick and Richard.

    Returns plain text. The PDF renderer escapes the ASSEMBLED string for HTML rather than
    escaping the fields before they get here, so the escape still covers any field added later.
    """
    received_from = _sender_clause(attorney_name, lawfirm)
    letter = _letter_clause(letter_type, letter_date)

    opening = (
        f"I have received {letter}{num_pages} pages of medical records{received_from}. "
        "I have reviewed all of the pages received and my opinion is based upon such records."
    )

    reviewer = (reviewer_name or "").strip()
    if not reviewer:
        return opening
    return (
        f"{opening} The initial organization, outlining, and excerpting of medical records "
        f"were performed by {reviewer}, {REVIEWER_CREDENTIAL}. I personally reviewed the "
        "excerpts, the entire outline, and the pages that were received, making additional "
        "inquiries and examinations as necessary to determine the relevant medical issues. "
        f"{LABOR_CODE_CITE}"
    )


@dataclass(frozen=True)
class RecordAccounting:
    """What the tail of the letter says about the pages that were received.

    Both reference documents the reviewers sent (the outside MRR and its covering memo) close
    with the same three facts, and ours closed with none of them. They are also the sentences
    this project has spent months PARSING out of human deliverables to score itself against -
    "Of the N pages received, exactly M pages were remarked upon" is the exclusion sentence
    every categorization rule since #134 rests on. Generating it is the same arithmetic read
    the other way round.

    THE THREE BUCKETS ARE DISJOINT, and that is not a stylistic choice. A non-primary duplicate
    row is removed from the excluded set rather than counted twice, and `duplicate_pages` counts
    pages BEYOND the first copy rather than every page in the group. Both change what the letter
    SAYS: without them a duplicated page is announced as a duplicate and listed among the other
    documents, and the type list carries a title the reviewer already resolved away.

    THAT REASONING USED TO CITE THE ARITHMETIC AND THE CITATION WAS WRONG - @adrian-g, on review.
    It said remarked + other + duplicates equals the total received "only if a duplicated page is
    counted ONCE". But `pages_other` is a derived REMAINDER, not a counted bucket, so the sum
    holds by construction however the first two are split - and fails outright when the clamp
    below fires (remarked 260 + other 0 + duplicates 61 = 321, against 191 received). The
    reconciliation against their own reference record is still what showed the buckets are meant
    to be disjoint; it is just not evidence that this code makes them so. A wrong reason in a
    comment is the thing this file keeps being caught by.

    That last sentence holds only for a group a reviewer has RESOLVED - see `record_accounting`.
    Until then the copies are still being remarked upon, and the letter says so rather than
    announcing a duplicate nobody has confirmed.
    """

    pages_received: int
    pages_remarked: int
    excluded_types: tuple[str, ...]
    duplicate_pages: int

    @property
    def pages_other(self) -> int:
        """Pages that are neither remarked upon nor a duplicate copy. Never negative: the
        three counts come from one partition of the same rows, but `pages_received` is the
        PDF's own page count and a row set that does not cover the document would otherwise
        produce a negative remainder in the delivered sentence."""
        return max(0, self.pages_received - self.pages_remarked - self.duplicate_pages)


def _row_pages(row) -> int:
    """Pages a row spans, inclusive of both ends, and never negative."""
    return max(0, (row.end or 0) - (row.start or 0) + 1)


def _pages_received(stated: int, on_file: int | None, covered: int) -> int:
    """The denominator the closing sentences count against: the cover sheet, or the file.

    Its own function so the two failures it separates can be stated once - a DATA-ENTRY SLIP
    and an arithmetic impossibility - @adrian-g, on review, and he is right that they deserve
    different treatment.

    `pages_received` became a typed field the moment the letter started counting against it, and
    `accounting_sentences` goes silent when the rows cover more than it states. So `24` typed for
    `241` removed the closing accounting from the Word letter, the linked PDF AND the memo, with
    nothing on screen saying why.

    A stated figure SMALLER than the pages the reviewer marked up cannot be right, and the file's
    own length is not in doubt - so when the rows fit inside the file but not inside the typed
    number, the typed number is the suspect and the file's count is used instead. Rows covering
    more than the FILE is the genuine impossibility #306 added the guard for, and that still goes
    quiet.

    A figure too LARGE is deliberately left alone. It prints "Of the 2410 pages received", which a
    reviewer notices; clamping it to the file would hide the same transposition the unreadable
    direction makes invisible.

    THIS IS NOW THE ONLY PROTECTION, where it used to be the second of two. The memo carried a
    sentence reporting the cover sheet against the file, so a transposed digit read "states 24
    pages and the file received contains 244 - 220 fewer than stated" and a reader saw it; the
    reviewers asked for that paragraph to come out (see `_memo_body`). The accounting still
    refuses to be blanked by a slip, but nothing surfaces the slip itself any more.
    """
    received = max(0, stated or 0)
    file_pages = max(0, on_file or 0)
    if file_pages and received < covered <= file_pages:
        return file_pages
    return received


def record_accounting(
    rows, pages_received: int, pages_on_file: int | None = None
) -> RecordAccounting:
    """Partition the reviewer's rows into remarked / other / duplicate.

    ``pages_on_file`` is the PDF's own length, and it only reaches `_pages_received`, which is
    where the cover sheet and the file are weighed against each other.

    Takes ORM rows rather than `as_row()` dicts deliberately: `ROW_FIELDS` carries neither
    `include` nor the duplicate columns, which is the same omission #258 had to work around,
    so a dict would not carry the two fields this needs.

    A non-primary member of a confirmed duplicate group is a duplicate FIRST, whatever its
    include flag says - `resolve_duplicate` leaves those rows excluded, so counting them as
    "other documents" as well is exactly the double count the disjoint arithmetic forbids.
    """
    remarked = duplicate = 0
    excluded: list[str] = []
    # A group counts only once a reviewer has actually RESOLVED it, which is what setting a
    # primary means. `resolve_duplicate` has three outcomes and only one of them makes a
    # surplus copy: keep_one marks exactly one member primary and excludes the rest; dismiss
    # sets `dupe_dismissed` and clears every primary, because the reviewer has said these are
    # NOT duplicates; and an untouched group is a suggestion nobody has acted on. Reading
    # `not dupe_primary` alone treats all three alike - measured on the box, 102 of 147 groups
    # have no primary at all, so that reading counted 1,140 pages where 171 are surplus copies,
    # and it would announce a reviewer's DISMISSED group to the client as duplicate copies.
    #
    # Dismissal needs no separate test here: dismiss clears every primary and keep_one clears
    # every dismissal, so a dismissed group can never appear in this set.
    #
    # `bundles.resolved_clusters` / `is_resolved_duplicate` already draw this exact distinction
    # for the bundle deliverable, and that docstring records @adrian-g catching the row-in-
    # isolation reading on review. They read `as_row()` DICTS and this reads ORM rows - the two
    # fields it needs are the two `ROW_FIELDS` omits - so the predicate is stated twice rather
    # than shared. `test_the_two_readings_of_a_resolved_duplicate_agree` pins them together, so
    # editing one without the other fails rather than drifting.
    resolved = {row.dupe_group for row in rows if row.dupe_group is not None and row.dupe_primary}
    for row in rows:
        pages = _row_pages(row)
        if row.dupe_group in resolved and not row.dupe_primary:
            duplicate += pages
            continue
        if row.include:
            remarked += pages
            continue
        title = (row.title or "").strip()
        if title:
            excluded.append(title)
    seen: dict[str, None] = {}
    for title in excluded:
        # Case-folded for de-duplication only; the FIRST spelling is what ships, because the
        # reference list is lower case prose and a title may legitimately carry an acronym.
        seen.setdefault(title.casefold(), None)
        seen[title.casefold()] = seen[title.casefold()] or title
    ordered = tuple(dict.fromkeys(v for v in seen.values() if v))
    received = _pages_received(pages_received, pages_on_file, remarked + duplicate)
    return RecordAccounting(
        pages_received=received,
        pages_remarked=remarked,
        excluded_types=ordered,
        duplicate_pages=duplicate,
    )


def accounting_sentences(accounting: RecordAccounting | None) -> tuple[str, str]:
    """The two bold sentences that close the letter, or "" for one that does not apply.

    CONDITIONAL, and that is observed rather than assumed: the reference MRR carries a
    duplicates sentence and the two supplemental reports for another patient carry none at all,
    so a count of zero means the sentence is absent, not that it reads "0 pages".

    Returned as text and rendered by each caller, so the Word and PDF renderers cannot drift
    apart on the WORDS the way they twice drifted on formatting (#158, #268).

    THREE SHAPES, because a template built for the usual record contradicts itself on the others
    - all three found on review by @adrian-g, each reproduced by calling this function:

    * The list is introduced with `such as:` ONLY when there is a list. Every excluded row can
      carry a blank title, which leaves `excluded_types` empty, and the sentence then ended on a
      dangling colon in front of a client - the shape of #115's "medical records from ." that
      `intro_sentence` and `summary_intro` both already guard against.
    * With nothing left over, the remainder clause goes entirely rather than reading "0 pages
      are other documents". Reachable whenever the only excluded rows are duplicate copies.
    * NOTHING is emitted when the rows cover more than the record. `pages_other` clamps at zero,
      so the sentence otherwise said "exactly 200 pages were remarked upon" of 191 received and
      then "0 pages are other documents such as:" above a list of two. The duplicates sentence
      goes with it: it opens "In addition", so it cannot be the only thing that ships, and a row
      set this broken does not support its count either.
    """
    if accounting is None or not accounting.pages_received:
        return "", ""
    if accounting.pages_remarked + accounting.duplicate_pages > accounting.pages_received:
        return "", ""
    counted = (
        f"Of the {accounting.pages_received} pages received, exactly "
        f"{accounting.pages_remarked} pages were remarked upon"
    )
    if not accounting.pages_other:
        exclusion = f"{counted}."
    elif accounting.excluded_types:
        exclusion = (
            f"{counted}, as the remaining {accounting.pages_other} pages are other "
            "documents such as:"
        )
    else:
        exclusion = (
            f"{counted}, as the remaining {accounting.pages_other} pages are other documents."
        )
    duplicates = (
        f"In addition, the records included {accounting.duplicate_pages} pages of duplicate "
        "copies of records already counted above."
        if accounting.duplicate_pages
        else ""
    )
    return exclusion, duplicates


@dataclass(frozen=True)
class MemoDetails:
    """Who the covering memo is addressed to, about what, and from whom.

    EVERY FIELD COMES OFF THE RECORD OR THE SESSION - none of it is asked for again. A first
    version put a doctor box and a received-on box in the export dialog, which was wrong twice
    over: the doctor is already a dropdown on the review page (it picks the report's typeface),
    and the reviewer running the export is already known from their account. Asking for either
    at download time is friction that also lets the memo disagree with the report beside it.

    Empty means ABSENT rather than blank throughout - the memo drops a line it cannot fill
    rather than printing a dangling label, which is the convention `intro_sentence` already
    follows for the law firm."""

    doctor: str = ""
    patient_name: str = ""
    attorney_name: str = ""
    lawfirm: str = ""
    letter_type: str = ""
    letter_date: str = ""
    reviewer_name: str = ""
    memo_date: str = ""
    # The two counts the memo exists to compare. `pages_stated` is the reviewer-entered cover
    # sheet figure and 0 means NOBODY HAS SAID, which is why it is not defaulted to the file's
    # own count: a confirmation that the two agree would then be the memo agreeing with itself.
    pages_stated: int = 0
    pages_on_file: int = 0


MEMO_GREETING = "Greetings."
MEMO_THANKS = "Thank you."
MEMO_VERIFIED_BY = "Verified by:"
MEMO_SOURCES_LEAD = "records from various sources:"


def memo_opening(accounting, details: MemoDetails) -> str:
    """The memo's first sentence: what arrived and from whom.

    Their memo and their report open with the same sentence in different voices - "I have
    received a defense advocacy letter dated 07/31/26 along with 191 pages of medical records
    from ..." on the report, `We have received ...` on the memo, because one is the evaluator
    writing and the other the office. So the WORDS differ by one pronoun and nothing else, and
    both halves that carry a fact are the letter's own: `_letter_clause` and `_sender_clause`.
    A memo naming the firm or the covering letter differently from the report stapled to it is
    the drift that cost #158 and #162, and reusing both is what stops it.

    Every clause is dropped when its field is empty, so this reads correctly on a record where
    only the page count is known.

    THE COUNT CLAUSE IS ONE OF THOSE CLAUSES, and it was not - @adrian-g, on review. This read
    `accounting.pages_received if accounting else 0`, and `_record_accounting` returns None for a
    record with no rows, which the memo route reaches BY DESIGN: it has no 409 because a record
    still being worked has a memo. So a memo could open "We have received 0 pages of medical
    records." while the paragraph below it said "The cover sheet states 241 pages" - two
    sentences on one page contradicting each other. That paragraph has since been removed at the
    reviewers' request, so the contradiction is no longer visible; the clause is still wrong
    without this, and it is now the ONLY place the memo states how many pages arrived.

    So the count falls back the way everything else here does: the accounting, then the cover
    sheet, then the file, and with none of the three the clause is dropped rather than printing a
    number nobody supplied."""
    pages = (accounting.pages_received if accounting else 0) or details.pages_stated
    pages = pages or details.pages_on_file
    letter = _letter_clause(details.letter_type, details.letter_date)
    sender = _sender_clause(details.attorney_name, details.lawfirm)
    counted = f"{pages} pages of " if pages else ""
    return f"We have received {letter}{counted}medical records{sender}."


def memo_header_lines(details: MemoDetails) -> list[tuple[str, str]]:
    """The addressed block, minus any line whose field is empty.

    DELIBERATELY THIN. Asked what this memo needed, the reviewers said the header "is not too
    important, we can ignore that for now" - so it carries only what the record already knows
    and asks for nothing. An earlier version added a doctor box and a received-on box to the
    export dialog to fill it, which spent UI friction on the half they told us to ignore and
    left out the page count they called the main thing.

    `TO: DR. <NAME>\u2019S OFFICE` is how theirs is addressed. A memo on a record with no
    doctor recorded drops the line rather than addressing the reader as `DR. \u2019S OFFICE`,
    which is the same reason the letter drops its law-firm clause."""
    rows: list[tuple[str, str]] = []
    doctor = (details.doctor or "").strip()
    if doctor:
        rows.append(("TO:", f"DR. {doctor.upper()}\u2019S OFFICE"))
    reviewer = (details.reviewer_name or "").strip()
    if reviewer:
        rows.append(("FROM:", reviewer))
    patient = (details.patient_name or "").strip()
    if patient:
        rows.append(("RE:", f"Review of {patient}"))
    memo_date = (details.memo_date or "").strip()
    if memo_date:
        rows.append(("DATE:", memo_date))
    return rows


def _memo_body(doc, accounting) -> None:
    """The accounting paragraphs, each present only when it applies.

    Takes no `MemoDetails`: with the cover-sheet comparison gone (below) every remaining
    sentence comes from the accounting, and the details object was left behind as an unused
    parameter.

    THE COVER-SHEET COMPARISON IS DELIBERATELY ABSENT, and it used to lead this section.

    It read "The cover sheet states 50 pages and the file received contains 52 pages - 2 more
    than stated. The page count above follows the cover sheet.", and the reviewers asked for it
    to come out. It was built on their earlier answer that "the main thing would be the actual
    page count vs declared page count", which we read as a sentence to print; the rest of that
    same answer says why it is not - "we should get the actual page count from the cover sheet
    since there are sometimes additional pages attached by us on the pdf". The gap is their own
    attached pages. Measured against four human deliverables it is 311/309, 293/290, 244/241 and
    229/226, i.e. present nearly every time, so the memo was reporting their routine padding back
    to the doctor's office as though it were a finding.

    The reconciliation itself is NOT lost, and this is the part to check before re-adding
    anything: `record_accounting` still takes `pages_on_file` and still treats the typed figure
    as the suspect when the rows fit inside the file but not inside it, which is what stops a
    mistyped cover-sheet number silently blanking the closing sentences of all three
    deliverables. What is gone is only the sentence that showed the gap to the reader.

    What that costs: nothing now surfaces a MISTYPED figure to a human. The accounting is
    guarded, but a reviewer who types 500 for 50 sees no sign of it. If that wants solving it
    belongs beside the field where the number is typed, not in a document a client reads.

    The figure itself still has to be TYPED: reading it out of the declaration was measured on
    the box and does not work - of 245 declaration and cover-sheet rows, 94 have no stored page
    text at all, 130 state no page count, and only 21 (8.6%) give a figure.

    `pages_stated` and `pages_on_file` are still live - `memo_opening` falls back to them for
    its count clause - so they are not dead with this gone."""
    exclusion_text, duplicates_text = accounting_sentences(accounting)
    if exclusion_text:
        doc.add_paragraph("")
        _run(doc.add_paragraph(), exclusion_text, bold=True)
        doc.add_paragraph("")
        _run(doc.add_paragraph(), MEMO_SOURCES_LEAD)
        doc.add_paragraph("")
        for excluded in accounting.excluded_types:
            # Plain, one per line. The list is not bold in theirs even though the sentence
            # introducing it is.
            _run(doc.add_paragraph(), excluded)
    if duplicates_text:
        doc.add_paragraph("")
        _run(doc.add_paragraph(), duplicates_text, bold=True)


def build_memo_document(accounting, details: MemoDetails | None = None):
    """The covering memo, as a Word document.

    Its middle sentences are the SAME page accounting the letter closes with, read from the
    same `RecordAccounting` rather than recomputed. A memo disagreeing with its own letter
    about how many pages arrived is the defect this file keeps finding in other forms, and it
    would be the one a client noticed first.

    TWO SENTENCES OF THEIRS ARE DELIBERATELY ABSENT.

    `Because of this, we are submitting a 7-page report, 5 pages of which comprise the record
    review` counts the pages of OUR OWN deliverable, and python-docx does not paginate - Word
    decides that when it opens the file. Inventing the number in a document a client reads is
    worse than leaving the sentence out.

    `Also, a total of N pages of previously reviewed reports ... were received` needs a record
    of what an earlier review covered, and nothing in this system stores one. That is its own
    piece of work rather than a formatting one."""
    details = details or MemoDetails()
    doc = Document()

    for label, value in memo_header_lines(details):
        paragraph = doc.add_paragraph()
        _run(paragraph, f"{label}\t")
        _run(paragraph, value)

    doc.add_paragraph("")
    _run(doc.add_paragraph(), MEMO_GREETING)
    doc.add_paragraph("")
    _run(doc.add_paragraph(), memo_opening(accounting, details))

    _memo_body(doc, accounting)

    doc.add_paragraph("")
    _run(doc.add_paragraph(), MEMO_THANKS)
    doc.add_paragraph("")
    _run(doc.add_paragraph(), MEMO_VERIFIED_BY)
    reviewer = (details.reviewer_name or "").strip()
    if reviewer:
        _run(doc.add_paragraph(), reviewer)
    memo_date = (details.memo_date or "").strip()
    if memo_date:
        _run(doc.add_paragraph(), memo_date)
    return doc


def summary_intro(lawfirm=None) -> str:
    """The line that introduces the entries.

    The reference document names the sending firm - "The following is a summary of records from
    <firm>:" - where ours said only "those records". The firm is OPTIONAL free text a reviewer
    often has no value for, so the clause is DROPPED rather than rendered empty, which is the
    convention `intro_sentence` already applies after #115 shipped "records from ." to a client.
    """
    firm = (lawfirm or "").strip()
    return f"The following is a summary of records from {firm}:" if firm else SUMMARY_INTRO


# Inline emphasis the summarizer emits: **bold**, *italic*, _italic_. Rendered as real runs so no
# raw markers leak into the Word document.
#
# THREE renderers read this - here, `linked_pdf._inline_html`, and the web's `MarkdownText` - and
# each held its own copy. `linked_pdf` now imports this one; the web's is in TypeScript and cannot,
# so `markdown-text.tsx` carries a comment naming this as the definition it tracks. The same file
# pair has diverged twice before (#158 on the heading and separator, #268 on the letter's
# alignment), which is why this is imported rather than repeated a third time.
#
# NOT re.DOTALL, and that is the fix rather than an omission. With it, `\*(.+?)\*` pairs a BULLET on
# one line with the bullet on the next - `* item` / `* item` - and italicises everything between.
# Measured over 3,017 stored summaries: 3 disagreed with the web renderer, and 6 of the 9 offending
# spans opened with "* " (a bullet, not emphasis), italicising 83 to 387 characters of a delivered
# document. The two remaining `**` spans were a wrapped bold heading, which now shows its markers
# exactly as the review screen already shows them - visible to the reviewer and editable, which a
# silently italicised paragraph is not. Emphasis does not cross a line break.
INLINE_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_")


# The two tiers the reviewers' own reports use, read off the reference MRR they supplied
# on 2026-09-14 - at run level, over its thirteen entries:
#
#   KEY       Diagnoses, Work Status, Treatment Plan, Return to Clinic.
#             The label is BOLD + UNDERLINED and the text that follows it is bold too.
#   ORDINARY  every other label - DOI, Physical Examination, Complaints, Treatment
#             Progress. The label is UNDERLINED only and its text is plain.
#
# The rule underneath is semantic rather than a list: what was FOUND and what HAPPENS NEXT
# is emphasised whole, while descriptive context gets an underlined label and a plain body.
#
# Measured against 3,133 delivered summaries before adopting it: `diagnoses` 1,034,
# `treatment plan` 1,031, `work status` 802 and the singular `diagnosis` 557, so 54.3% of
# summaries carry at least one. `return to clinic` is theirs and appears in NONE of ours -
# carried anyway, so their wording is already answered if it starts.
KEY_ENTRY_LABELS: frozenset[str] = frozenset(
    {"diagnoses", "diagnosis", "work status", "treatment plan", "return to clinic"}
)


def _label_key(label: str) -> str:
    r"""A bold span reduced to the form KEY_ENTRY_LABELS is written in.

    Ours are not spelled consistently - `Diagnosis` beside `Diagnoses`, `Physical Exam`
    beside `Physical Examination` - and a trailing colon is optional.

    `str.strip` rather than a regex. `re.sub(r"[\s:.\-]+$", ...)` does the same job and is
    super-linear: a repeated character class anchored at the end makes the engine retry from
    every position on a label that does not end in one of those characters, which is most of
    them. Sonar refused it as python:S8786, and it is the same shape as the leading `\s*` in
    a substitution pattern that #162 had to bound. `strip` is one linear pass."""
    return (label or "").lower().strip(" \t\r\n:.-")


def entry_body_segments(text: str) -> list[tuple[str, bool, bool, bool]]:
    """One entry body as ``(text, bold, italic, underline)`` runs, in the two tiers above.

    ONE parser, two emitters: `build_mrr_document` turns these into Word runs and
    `linked_pdf` into <b>/<i>/<u> spans. Three separate copies of an emphasis walk is what
    #284 had to undo, so the classification is made once and both renderers read it.

    Every `**span**` is treated as a LABEL, because in the reviewers' document the
    underlined runs ARE the labels. A model that bolds something mid-sentence therefore
    gets it underlined rather than bold - visible and harmless, and the alternative is
    guessing which bold spans are headings."""
    segments: list[tuple[str, bool, bool, bool]] = []
    pos, carry_bold = 0, False
    for m in INLINE_EMPHASIS_RE.finditer(text or ""):
        if m.start() > pos:
            segments.append((text[pos : m.start()], carry_bold, False, False))
        if m.group(1) is not None:
            carry_bold = _label_key(m.group(1)) in KEY_ENTRY_LABELS
            segments.append((m.group(1), carry_bold, False, True))
        else:
            segments.append((m.group(2) or m.group(3), carry_bold, True, False))
        pos = m.end()
    if pos < len(text or ""):
        segments.append((text[pos:], carry_bold, False, False))
    return [s for s in segments if s[0]]


def _run(paragraph, s, *, bold=False, italic=False, size=None, underline=False, font=None):
    run = paragraph.add_run(s)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    run.font.name = font or _REPORT_FONT
    run.font.size = size or Pt(11)
    return run


def _add_inline_runs(paragraph, text, *, bold=False, italic=False, font=None):
    """Append runs to ``paragraph``, turning **bold** / *italic* / _italic_ markers into real
    formatting; ``bold``/``italic`` set the baseline for the plain segments."""
    pos = 0
    for m in INLINE_EMPHASIS_RE.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos : m.start()], bold=bold, italic=italic, font=font)
        if m.group(1) is not None:
            _run(paragraph, m.group(1), bold=True, italic=italic, font=font)
        else:
            _run(paragraph, m.group(2) or m.group(3), bold=bold, italic=True, font=font)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold=bold, italic=italic, font=font)


def _page_number_field(paragraph) -> None:
    """Append a real Word PAGE field, so the header numbers the pages.

    The header used to end with the literal string ``"Page "`` and nothing after it: python-docx
    writes text, not fields, so every page of the delivered .docx carried a dangling label with no
    number while the linked PDF numbered its pages properly. A field rather than a computed integer
    because a Word header is one object repeated on every page - there is no per-page text to write,
    which is presumably how the bare label got there.

    ``w:fldSimple`` with a cached ``1`` inside: Word and LibreOffice both recalculate the field on
    open, and the cached run is what a reader that does not evaluate fields shows instead of nothing.
    """
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), " PAGE ")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def _letter_paragraph(doc, text, *, bold=False, underline=False, centered=False, font=None):
    """One styled paragraph of the letter: its whole run formatting decided in one place.

    Five paragraphs each styled their own run with the same six lines, and the copies had
    already drifted. `nine_title_format = fourth_title.runs[0]` read the WRONG paragraph's
    run, which cost two visible defects in one name: the conclusion got no formatting at all
    and shipped in python-docx's default Calibri 11 while every other paragraph is Times New
    Roman 12, and its `bold = False` UNDID the `bold = True` set on SUMMARY_INTRO thirty lines
    above - so the .docx and the .pdf disagreed on the formatting of a sentence the client
    reads. ruff had already found it (F841, a local assigned and never used, which IS the bug)
    and it was silenced with a noqa rather than fixed. One helper is what stops the sixth
    paragraph being written by hand.

    `alignment` is a PARAGRAPH property, so it is set on the paragraph and not on the run;
    assigning it to a run is silently a no-op. Both were done at the two call sites that
    wanted LEFT, which is also the default - so nothing was visible, but the CENTER one would
    have failed the same way."""
    paragraph = doc.add_paragraph("")
    run = _run(paragraph, text, bold=bold, size=Pt(12), font=font)
    run.underline = underline
    paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER if centered else WD_PARAGRAPH_ALIGNMENT.LEFT
    return paragraph


def _fill_header(header, re_line, dob_line, *, numbered: bool, font=None) -> None:
    """Write the identifying lines into ``header``, optionally followed by ``Page <n>``.

    Writes into the header's EXISTING first paragraph rather than adding one. A new Word header
    already carries an empty paragraph, so `add_paragraph` left a blank line above the patient's
    name on every page of the deliverable.
    """
    paragraph = header.paragraphs[0]
    # Removing the run ELEMENTS, not `paragraph.text = ""` - that assignment leaves one empty run
    # behind, so the first run of the header carried no font at all and `runs[0]` was not the text.
    # Safe to iterate while deleting: `runs` builds a fresh list from the XML on every access, so
    # this is already a snapshot and the copy that used to wrap it added nothing.
    for run in paragraph.runs:
        run._element.getparent().remove(run._element)
    _run(
        paragraph,
        f"{re_line}\n{dob_line}" + ("\nPage " if numbered else ""),
        size=Pt(10),
        font=font,
    )
    if numbered:
        _page_number_field(paragraph)


UNDATED_LABEL = "Undated"


# Every separator and year width these records actually use. ONE format was accepted before -
# `%m/%d/%Y` - and everything else became "Undated", which a senior reviewer found on a delivered
# record: "It had some dates under the XX-XX-20XX format instead of XX/XX/20XX format."
#
# The 2-digit variants are NOT speculative padding, they were failing too: `%Y` needs four digits,
# so `09/22/26` parsed as nothing and a dated document rendered Undated AND sorted to the end.
#
# ORDER MATTERS, 4-digit before 2-digit. `%y` pivots at 1969-2068, so a four-digit year offered to
# it first would be misread rather than rejected.
#
# NOT extended to spelled-out months or ambiguous DD/MM. A reviewer compares this against the page,
# and guessing between 03/04 and 04/03 to rescue a date is the kind of help nobody asked for.
_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%Y-%m-%d",
    "%m/%d/%y",
    "%m-%d-%y",
    "%m.%d.%y",
)


def parsed_date(entry):
    """The entry's date as a datetime, or None when it states none.

    ONE definition of "undated", shared by the sort key and the label. Written separately they
    disagreed: an entry dated "n/a" sorted last (unparseable) but rendered the raw "n/a", because the
    label only special-cased "-" and "". So an entry could sort as undated while displaying junk. Not
    reachable from today's field spec, which only ever writes "-", but two definitions of one concept
    drift eventually.
    """
    value = (entry.get("summaryDate") or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def date_label(entry) -> str:
    """The date cell text: the date exactly as written, or "Undated".

    The field spec writes "-" when a document carries no date, which in a finished deliverable reads
    as a value nobody filled in rather than a fact about the document. The reviewers call these
    Undated and expect them at the end of the review.

    A parsed date is returned VERBATIM, never reformatted: the factuality rules say copy dates
    exactly, and a reviewer compares them against the page.
    """
    if parsed_date(entry) is None:
        return UNDATED_LABEL
    return (entry.get("summaryDate") or "").strip()


def build_mrr_document(
    entries,
    num_pages,
    patient_name,
    patient_dob,
    qme_or_ame,
    *,
    details: ReportDetails | None = None,
):
    """Assemble the MRR Word document from summary ``entries`` (sorted chronologically).

    ``details`` carries the doctor, the covering letter and the page accounting. Omitted, the
    document renders exactly as it did before any of those existed - which is what the bundle
    export wants: it builds a letter from rows selected by CATEGORY rather than from a whole
    record, where a sentence about the pages RECEIVED would answer a question nobody asked."""
    details = details or ReportDetails()
    font = report_font(details.doctor)
    lawfirm = details.lawfirm

    # Undated entries sort LAST, per the reviewers 2026-08-21: "if it is something important we will
    # still summarize it, it can go at the end of the Review as Undated". This was datetime.min -
    # undated FIRST - so a document stating no date sorted ahead of the earliest real encounter and
    # the deliverable could OPEN on one.
    entries = sorted(entries, key=lambda e: parsed_date(e) or datetime.max)

    doc = Document()

    # HEADER
    section = doc.sections[0]
    # A separate first-page header, so page 1 carries the patient lines WITHOUT a page number and
    # every later page carries all three. That is what `linked_pdf._draw_running_header` already
    # does (`"" if i == 0 else f"\nPage {i + 1}"`), and matching it is the point: the two are the
    # same deliverable in two formats and a reviewer reads them side by side.
    section.different_first_page_header_footer = True
    re_line, dob_line = header_lines(patient_name, patient_dob)
    _fill_header(section.first_page_header, re_line, dob_line, numbered=False, font=font)
    _fill_header(section.header, re_line, dob_line, numbered=True, font=font)

    doc.add_paragraph("")

    # TITLE. The form allows an empty QME/AME field, and this used to guard a crash:
    # `title.runs[0]` on a paragraph built from an empty string raises, because an empty
    # paragraph has no runs. `_letter_paragraph` ADDS the run itself, so that is gone -
    # measured, not assumed. The fallback is kept only so the emitted document does not
    # change: both render as a blank line, but one writes a space and one writes nothing.
    _letter_paragraph(doc, qme_or_ame or " ", bold=True, underline=True, centered=True, font=font)

    doc.add_paragraph("")

    _letter_paragraph(doc, REVIEW_HEADING, bold=True, underline=True, font=font)
    _letter_paragraph(
        doc,
        intro_sentence(
            num_pages,
            lawfirm,
            attorney_name=details.attorney_name,
            letter_type=details.letter_type,
            letter_date=details.letter_date,
            reviewer_name=details.reviewer_name,
        ),
        font=font,
    )
    _letter_paragraph(doc, summary_intro(lawfirm), bold=True, font=font)

    # Two-column borderless table: date | title + body. The default "Table Normal" style has no
    # cell borders, matching the canonical MRR summary layout (date sits in its own left column,
    # the summary flows in the right column) instead of the old inline date-tab-title paragraph.
    table = doc.add_table(rows=0, cols=2)
    table.autofit = False
    for entry in entries:
        cells = table.add_row().cells
        cells[0].width = Inches(0.9)
        cells[1].width = Inches(5.6)
        cells[0].vertical_alignment = WD_ALIGN_VERTICAL.TOP
        cells[1].vertical_alignment = WD_ALIGN_VERTICAL.TOP
        _run(cells[0].paragraphs[0], date_label(entry), font=font)
        body = cells[1].paragraphs[0]
        # Justified: the report is read as a finished document, and a ragged right edge on every
        # record is what made the export look like a draft.
        body.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        # The entry header is PLAIN in the reviewers' own reports - date, author, facility
        # and type read as a sentence rather than a heading. Ours bolded it.
        _add_inline_runs(body, entry["summaryTitle"], font=font)
        _run(body, TITLE_SEPARATOR, font=font)
        for chunk, bold, italic, underline in entry_body_segments(entry["summaryText"]):
            _run(body, chunk, bold=bold, italic=italic, underline=underline, font=font)

    # THE PAGE ACCOUNTING, between the entries and the conclusion - the position both
    # reference documents put it in. Bold, because they bold all three of these sentences
    # while leaving the type list beneath plain; that contrast is the whole of the house
    # style here and rendering the list bold too would lose it.
    exclusion_text, duplicates_text = accounting_sentences(details.accounting)
    if exclusion_text:
        _letter_paragraph(doc, exclusion_text, bold=True, font=font)
        for document_type in details.accounting.excluded_types:
            _letter_paragraph(doc, document_type, font=font)
    if duplicates_text:
        _letter_paragraph(doc, duplicates_text, bold=True, font=font)

    # This paragraph is why `_letter_paragraph` exists - it used to style the WRONG run, and
    # the helper's docstring records what that cost.
    _letter_paragraph(doc, CONCLUSION, font=font)

    return doc
