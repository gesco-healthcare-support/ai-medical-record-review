"""B5 categorization cascade: deterministic rules -> local embeddings -> Gemini enum (ported).

A sub-document title (and, on low confidence, its first-page OCR text) is classified into a
category id. Conflicting or weak results are flagged for manual review rather than silently
bucketed into the catch-all.

torch/sentence-transformers is imported lazily (only when the embedding stage runs), so importing
this module does not pull in torch - the WEB tier never runs classify(); only the segment worker
does. The catalog is read on SHORT-LIVED sessions (get_sessionmaker) so classify() stays
thread-safe under the CLASSIFY_WORKERS pool and needs no Flask app context (the worker's own
per-process engine backs it).
"""

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np
from google.genai import types

from app.config import get_settings
from app.db import get_sessionmaker
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.taxonomy import CATEGORIES, DEFAULT_ID
from app.worker.failures import JobCancelled

logger = logging.getLogger(__name__)

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# Administrative paperwork that wraps a record: routing slips, cover letters, emails, declarations,
# proofs of service, records requests, appointment/evaluation notices. These are checked SEPARATELY
# from the document-type rules below (see match_rules), because such a title routinely names the
# document it accompanies - "Cover Letter - PR-2 Progress Report" is a progress report with a cover
# page, while "Email - AME Evaluation Cover Letter" is only correspondence. General (100) is
# unchecked for summarization by default, so claiming the first of those would silently drop a real
# report from the summary.
_ADMIN_RULES: tuple[re.Pattern, ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"routing (sheet|slip|form)|records? routing",
        # `(ame|qme|pqme) letter` added 2026-08-18. A letter APPOINTING or instructing an evaluator
        # is procedural, but only "cover"/"transmittal" were listed, so "Joint AME Letter" matched no
        # administrative rule at all and the bare `ame` in rule 13 answered it. Safe because
        # _EVALUATOR_MENTION withholds ONLY 13: a real document type in the same title still wins, so
        # "Supplemental AME Letter" keeps 12.
        # `e-?mails?\b` UNANCHORED at the end, 2026-08-31. `^\s*e-?mail\s*$` matched only a title
        # that is EXACTLY the word, so anything an email is ABOUT fell through - and where that
        # something was an evaluator, the bare `ame`/`qme` in rule 13 answered it and the email was
        # summarized as the evaluation:
        #
        #     Email - QME Report/records          -> 13     found in a real record, delivered
        #     Email - AME Evaluation              -> 13
        #     Email Chain / Emails                -> no rule at all
        #
        # This is the SAME defect as `(ame|qme|pqme) letter` above and the same argument word for
        # word: a message ABOUT an evaluation is procedural, only the bare form was listed, so the
        # evaluator mention won. Fixed there in #119 and missed here.
        #
        # Still anchored at the START. An email is one when the title OPENS with it; a clinical
        # document that merely mentions email later is not, and `\b` at the end keeps "emailed"
        # from matching. Confirmed by the reviewers, who name "mails" in an excluded-types list.
        r"\b(cover|transmittal) letter\b|\b(ame|qme|pqme) letter\b"
        r"|\bcorrespondence\b|^\s*e-?mails?\b",
        # `declaration of service` added UNANCHORED 2026-08-18. `^declaration\b` only matches a title
        # that STARTS with the word, so "QME Declaration of Service" was answered 13 by the evaluator
        # mention while a bare "Declaration of Service" was answered 100 - the same document, decided
        # by word order.
        #
        # Both additions are confirmed against the human deliverable for record 7fb2b543, whose own
        # list of pages NOT remarked upon names "joint AME letter" and "AME or QME declaration of
        # service of medical legal report". See the NOT-fixed note below for the second one.
        r"^declaration\b|\bdeclaration of service\b|proof of service"
        r"|certificate of (service|mailing)|declaration under penalty",
        r"schedule of records|index of records|records? (request|index)"
        r"|request for (medical )?records",
        r"\b(request|notice|scheduling) (for |of |to )?[\w\s-]{0,24}\b(evaluation|examination)\b"
        r"|\b(evaluation|examination) (request|notice|appointment)\b",
        # HOSPITAL AND REGISTRATION PAPERWORK, added 2026-08-19. Every phrase here is named VERBATIM in
        # the excluded-pages list of a human deliverable, so this is the reviewer's own answer rather
        # than our reading of it. Two records, 267 and 300 pages:
        #
        #   "records from Providence St Joseph Medical Center: facesheet, ED care timeline, medication
        #    administration, order list, flowsheets, after visit summary, conditions of admission,
        #    ER registration form, Spanish documents, patient information sheet, coding summary,
        #    interdisciplinary notes"
        #   "...patient referral, emergency patient record, discharge report, patient signature page"
        #
        # These already reach 100 through the cascade MOST of the time, which is exactly the problem: no
        # rule answers them, so each occurrence is re-decided and the answer is not stable. Measured on
        # one 267-page record, "WORK STATUS REPORT" appeared ten times and got category 1 nine times and
        # 100 once; "lab order" got 100 and 3 on two occurrences. A rule makes the answer the same every
        # time, which is the point - not moving them somewhere new.
        #
        # Multi-word phrases deliberately, not single words. "admission" alone would catch "Conditions
        # of Admission" (correct) but also anything mentioning an admission; "facesheet" is unambiguous
        # but "summary" and "record" are not, so each is anchored to the phrase the human wrote.
        #
        # Three types the human's list also names are NOT here, and none of them belongs here:
        #
        #   "Physician's Return-to-Work & Voucher Report"  the reviewers want it SUMMARIZED - it is a
        #                                                  document-type rule in _RULES (-> 1), not
        #                                                  administrative paperwork. Asked 2026-08-21.
        #   "Interdisciplinary Notes"                      no rule: 3 titles, every row already 100
        #   "Transmittal Note"                             no rule: 1 title, every row already 100
        #
        # The last two are answered correctly by the cascade and cost nothing, so a rule would buy
        # determinism they are not visibly missing - and a rule hit skips the review flag. Both stay
        # pinned xfail so a rule appearing later still shows up.
        r"\bfacesheet\b|\bflowsheets?\b|\bafter visit summary\b|\bcoding summary\b"
        r"|\bpatient (referral|signature page|information sheet)\b"
        r"|\b(er|emergency room) registration\b|\bconditions of admission\b"
        r"|\b(admission|inpatient|emergency patient) record\b|\bmedication administration\b"
        r"|\bed care timeline\b",
        # EXCERPTED / REVIEWED RECORDS, and the anchors are the whole point (#222).
        #
        # The senior reviewer's instruction is unambiguous - "Yes, do not include the excerpted
        # medical records" (#159) - and we mostly obeyed it. But nothing PINNED the bare title, so
        # the cascade re-decided it on every record and eventually answered differently: two rows on
        # that reviewer's own account reached category 1, included, and shipped summaries, while the
        # identical normalised title sat at 100 on another record.
        #
        # #159 looked at this and deliberately declined to add a rule, and that reasoning was right
        # for the rule it considered. A SUBSTRING match on "excerpt" also claims the qualified
        # forms, and the same answer carves those out - he wants embedded studies summarized:
        #
        #   Medical Record Excerpt - MRI of Right Knee            -> 3, summarized, CORRECTLY
        #   Medical Record Excerpt - UCLA Medical Center          -> 2, summarized
        #   Medical Record Excerpt - ABC Occupational Med Center  -> 1, summarized
        #
        # `_DOCUMENT_NOUN` cannot rescue those, because "records" is deliberately absent from it
        # (see its comment), so a substring rule really would bury them. The two facility-qualified
        # forms are decided by the CASCADE with no `_RULES` match at all, which means an
        # administrative hit would send them to DEFAULT_ID and drop real content.
        #
        # So the predicate is not "contains the wrapper" but "IS the wrapper and nothing else". That
        # is testable against all five real titles above and it is why every alternative here is
        # anchored at both ends.
        #
        # `medical excerpted records index` is deliberately NOT matched: it currently reaches
        # category 3 and is not included, so nothing ships from it, and moving it would be a
        # behaviour change nobody asked for. Reported on #222 instead.
        r"^\s*medical\s+records?\s+excerpts?\s*$"
        + r"|^\s*(?:excerpted\s+medical|medical\s+excerpted)\s+records?\s*$"
        + r"|^\s*review\s+of\s+(?:the\s+)?medical\s+records?\s*$",
    )
)

# The one rule that fires on a MENTION of the evaluator rather than on a document type. A cover
# letter or an appointment notice about an AME says "AME" too, so this category alone does not
# outrank an administrative match - any other rule does.
_EVALUATOR_MENTION = "13"

# Document types that OUTRANK a bare evaluator mention, even with no administrative rule in play.
# Rule 13 sits second in _RULES and first-match-wins, so before this "AME Deposition Transcript"
# answered 13 rather than 9 - a transcript of the AME being QUESTIONED, filed as the AME's own
# report and summarized with the evaluation prompt, which asks for diagnoses, causation and
# apportionment that a transcript does not carry.
#
# Deliberately an explicit SET rather than moving rule 13 down the list. Reordering cannot express
# this: rules 1 and 2 sit ahead of these four, so any position that lets a deposition win also lets
# "progress report" and "permanent and stationary" win - and those legitimately describe the
# evaluator's OWN report ("AME Permanent and Stationary Report" is 13, not 2).
#
# 2026-08-18: decided in-house, NOT confirmed with eData. Reversible - drop an id to restore the
# previous answer for that shape.
_EVALUATOR_YIELDS_TO = frozenset({"3", "8", "9", "14"})  # imaging, operative, deposition, lab

# The same shape one rule lower down, and the same remedy. "follow-up" describes a VISIT'S PLACE IN A
# COURSE OF CARE, not a document type - a study, an operative report or a lab panel can each be a
# follow-up - but the token sits unqualified in the category-1 rule at index 7, ahead of imaging (9),
# operative (10), deposition (11) and laboratory (16). First match wins, so "Follow-Up MRI of the
# Lumbar Spine" answered 1, short-circuited before the embedding and LLM stages, and came back
# confidence='high' with needs_review False. The row is then summarized with the PR-2 treating prompt
# instead of the diagnostic one, and nothing flags it.
#
# Identical to the two defects this file has already fixed: `laborator` living in rule 3, which
# summarized a metabolic panel with the diagnostic prompt, and "Radiology Order" matching `radiolog`,
# an order FOR a study classified as the study itself.
#
# Withheld ONLY when follow-up is the sole reason category 1 matched. "Office Visit Follow-Up MRI"
# and "Progress Note - MRI Lumbar Spine" still answer 1, because those titles name a treating visit
# outright - the guard reads the REASON the rule fired, not merely that it did.
#
# MEASURED before shipping, on all 1,478 distinct titles on the box: ZERO change their answer, and
# zero titles carry a follow-up token beside a modality token at all. So this is a statement of
# intent for the day a title carries both, not a fix for observed loss - the same footing as the
# order-versus-imaging precedence in #151. The alternative, qualifying the token so it only matches
# "follow-up visit"/"follow-up appointment", was measured too and REJECTED: it moved 4 real titles
# from 1 to no-rule-at-all, spending live content to fix nothing.
_FOLLOWUP_MENTION = "1"
_FOLLOWUP_TOKEN = re.compile(r"follow ?-? ?up")
# The category-1 rule minus that token: did anything else in it match?
#
# THIS LIST AND RULE 1 ARE A PAIR AND MOVE TOGETHER. Nothing but this comment enforces it, and it
# has now been missed twice - `work status` arrived in rule 1 in #146 and `activity status` in this
# change, and neither came here on its own. The cost is silent and only shows on titles carrying
# BOTH a follow-up token and a modality, which is why no existing test caught it:
#
#     Work Status Report Follow-Up MRI          -> 1
#     Activity Status Report Follow-Up MRI      -> 3      the same document, two answers
#
# A token in rule 1 but not here means that synonym surrenders category 1 to imaging, operative,
# deposition or laboratory where its twin keeps it. Caught by @adrian-g on review, who replayed it:
# 4 of 4 such pairs diverged before, 0 of 4 after, and 72 titles move x -> 1, every one carrying
# both tokens.
_TREATING_VISIT_WITHOUT_FOLLOWUP = re.compile(
    r"\bpr-?2\b|progress report|progress note|office visit|work status|\bactivity status\b"
)
_FOLLOWUP_YIELDS_TO = frozenset({"3", "8", "9", "14"})  # imaging, operative, deposition, lab

# Words that name a DOCUMENT rather than the paperwork wrapped around it. The segmenter is told to
# fold a cover sheet into the document it travels with and to title the record from the visible
# header (services/gemini.py), so "Cover Letter - AME Report" is one record containing a report.
# When a title names a document like this, the administrative rules stand down and the normal
# cascade decides - including the embedding + LLM stages for report types no rule covers.
# "records" is deliberately absent: "Schedule of Records" and "Cover Letter - Submission of Medical
# Records" are paperwork about records, not records.
_DOCUMENT_NOUN = re.compile(
    r"\b(report|transcript|notes?|study|scan|imaging|x-? ?ray|chart|questionnaire|results?)\b"
)

# The one shape where a document noun names what the paperwork is ABOUT rather than what the pages
# ARE: "...Declaration of Service OF Medical - Legal Report". The noun is the object of the service,
# so _DOCUMENT_NOUN must not stand the administrative rule down for it. This is the case pinned
# xfail in #119 as needing a decision.
#
# Anchored on the trailing "of" and nothing wider, because a real evaluation genuinely DOES travel
# with a service page - "QME Report - Proof of Service" and "Panel QME Report with Declaration of
# Service" are category 13 and pinned as such in the suite. Word order is the whole distinction:
# paperwork-first with the document as its grammatical object, versus document-first with the
# paperwork attached. A broader test (treating every declaration as standalone) was written and
# measured first; it broke both of those pinned titles, which is what narrowed it to this.
#
# 2026-08-18: decided in-house, NOT confirmed with eData.
_PAPERWORK_ABOUT_A_DOCUMENT = re.compile(
    # NOT widened to cover emails, and the attempt is worth recording because it looked right.
    #
    # 2026-08-31 a real record delivered `Email - QME Report/records` as category 13 - summarized
    # with the evaluation checklist - and adding `^\s*e-?mails?\b` here fixes exactly that: the
    # `_DOCUMENT_NOUN` valve stops standing the administrative rule down on the word "Report", so
    # the email stays 100.
    #
    # It also breaks `test_evaluation_reports_survive_their_cover_page`, which pins
    # "Email Correspondence - QME Report" at 13 on purpose. That pin is the document-beats-wrapper
    # principle, and the title alone cannot separate the two readings: an email COVER PAGE on a
    # 40-page QME report, and a 2-page email ABOUT one, are the same string.
    #
    # The pin wins. SS15.7 measured this valve over 2,874 rows and found ZERO confirmed cases of a
    # real clinical document lost to a wrapper, and content hidden inside another record is never
    # surfaced again while a wrongly-kept email is visible to the reviewer. One observed row does not
    # buy reintroducing that risk. The row is reported instead.
    r"\b(declaration|proof|certificate) of (service|mailing) of\b"
)

# The return-to-work voucher, as ONE pattern requiring BOTH tokens in either order with a bounded
# gap. Used once, by the category-1 rule below.
#
# THE DESTINATION IS 1, NOT 100, and that was the answer coming back rather than our guess. The
# document is a form that travels with an evaluation packet, and the first reading here was that the
# reviewers exclude it: the 229-page record's excluded-pages list names it verbatim, and neither that
# report nor the 420-page one has an entry on its date. Asked directly on 2026-08-21, the answer was
# the opposite - they DO want it summarized, and mostly as a treating report. Where it arrives as its
# own document they summarize it separately, and where it arrives behind a report they merge the two,
# which is a boundary decision a category rule cannot express and the reviewer already makes by hand.
# So the rule takes the common case.
#
# NO LONGER PROVISIONAL. Confirmed by Adam 2026-08-24, asked directly: "I think it's easiest just to
# have them under the PR-2 category, unless we want to make an entirely new category. The PR-2
# category should have the right prompts to properly summarize the Return to Work reports." So 1
# stands, and the reason it stands is the prompt rather than the form's resemblance to a PR-2.
#
# BOTH TOKENS ARE REQUIRED, and that is the whole precision of it. `return[- ]to[- ]work` alone
# matches two real titles that are clinical documents in their own right - one category 1, one
# category 5 (physical therapy), one page each, both delivered - and claiming them for 1 would move
# the therapy note out of the category its own prompt is written for. `voucher` alone would match an
# evaluation that merely discusses one. Measured over every title on the box: 4 carry both tokens, 2
# carry only return-to-work, 0 carry only voucher, and 0 of any of them mention an evaluator.
#
# Either order, and the gap is BOUNDED rather than `.*`: an unbounded gap inside an alternation is
# the shape a ReDoS check flags, and 40 characters is far more than the observed " & " needs.
_RETURN_TO_WORK_VOUCHER = (
    r"return[- ]to[- ]work\b.{0,40}\bvoucher\b|\bvoucher\b.{0,40}return[- ]to[- ]work"
)

# Two types the #134 note named alongside it get NO rule, and that is measured rather than an
# omission: every observed row of both already answers 100 through the cascade - three distinct
# titles for "Interdisciplinary Notes", one for "Transmittal Note" - so neither is costing content
# and neither has earned a rule that would also skip the review flag.

# Ordered high-precision rules; first match wins. Specific categories precede the categories they
# could be confused with (e.g. supplemental QME/AME -> 12 before QME/AME -> 13).
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), category)
    for pattern, category in (
        # A REQUEST for a supplemental report is paperwork, not the report. Adam 2026-08-24: "The
        # letter requesting a supplemental can probably be skipped, as we are more concerned about the
        # supplemental report itself." Before this it answered 100 twice and 12 twice on four
        # occurrences - the same document type filed as the very report it is asking someone to write.
        #
        # DIRECTIONAL, and that is the whole safety of it: the request token must come BEFORE
        # `supplement`, within a bounded gap. "Supplemental Report in Response to Your Request" is the
        # actual report and carries the same two words in the other order, so an undirected pattern
        # would send a real medical-legal supplemental to 100 and drop it from the deliverable
        # entirely. That is the expensive direction of this mistake, so the pattern refuses it.
        #
        # Sits above rule 12 because 12 matches on `supplement` plus an evaluator token, which a
        # request for a QME supplemental also carries.
        (r"\b(?:request for|letter requesting|requesting)\b.{0,30}\bsupplement", "100"),
        (r"supplement\w*.{0,40}\b(qme|ame|pqme)\b|\b(qme|ame|pqme)\b.{0,40}supplement", "12"),
        (r"\b(qme|ame|pqme)\b|qualified medical evaluator|agreed medical evaluator", "13"),
        # Category 15 added 2026-08-21, answered by Adam. PLACEMENT IS THE DESIGN: after the
        # evaluator rules (a QME report discussing a utilization review is still the evaluation) and
        # before every clinical modality rule below (a determination ABOUT an MRI request is a
        # determination, not an MRI report; the same for physical therapy and progress reports).
        #
        # It sits above 10 deliberately too, even though no observed title contains both - measured
        # over every row on the box, zero utilization-review titles also say "RFA" or "request for
        # authorization", so the order decides nothing today and states the intent for the day a
        # title carries both. 10 is the treating physician ASKING; this is the answer coming back.
        #
        # Before this, these landed in 10 twelve times, 100 four times, 3 three times and 5 twice -
        # one document type answered four different ways, and on the one reviewed copy a human put
        # four identical documents into three different categories.
        (r"utilization review|independent medical review|\bimr\b", "15"),
        # The return-to-work voucher -> 1, asked and answered 2026-08-21. See
        # _RETURN_TO_WORK_VOUCHER above for why both tokens are required and why 1 is provisional.
        #
        # Placed AFTER the evaluator rules so "AME Report - Return-to-Work & Voucher" stays 13, and
        # BEFORE rule 1's own pattern for no reason that matters - the observed titles match neither
        # "progress report" nor "office visit", so this rule is what answers them either way.
        (_RETURN_TO_WORK_VOUCHER, "1"),
        # `shock[- ]?wave (therapy|treatment)` and `functional improvement` added 2026-08-20, both
        # answered by the eData reviewers who write these reports by hand - the first document-type
        # question we have asked them and had answered, rather than decided in-house.
        #
        # Extracorporeal Shockwave Treatment Report (an M.D. at a pain management practice) -> 5, and
        # Functional Improvement Measurements (an L.Ac.) -> 5. Both were answered 100 by the cascade
        # and 100 is unchecked for summarization, so all five occurrences reached no deliverable: four
        # 7-page shockwave reports and one 14-page measurement sheet, 42 pages in total.
        #
        # The shockwave half is independently confirmed: on the reviewed copy of that record the
        # reviewer changed all four rows from 100 to 5 by hand. The measurement sheet is NOT - the
        # reviewer put those same 14 pages in 3 (diagnostic studies and imaging) on his copy. eData's
        # answer is taken as authoritative here because 3 requires a study "reported as an image or a
        # tracing", which a measurement sheet is not, and because the author is an acupuncturist. The
        # disagreement is recorded rather than hidden; it is one row, and it is the kind of thing to
        # re-ask if it recurs.
        #
        # `therapy|treatment` is REQUIRED after the wave, and that is the whole precision of this
        # rule: extracorporeal shock wave LITHOTRIPSY is a urology procedure for kidney stones and
        # shares the first three words. A bare `extracorporeal shock ?wave` was measured first and
        # matched a constructed lithotripsy title, so it was narrowed to this. The narrowing also
        # leaves alone the one real row (user 4, 7 pages) that says shockwave with no therapy or
        # treatment word and sits at category 1 - no human has ruled on that one, so nothing here
        # moves it.
        (
            r"physical therapy|chiropractic|chiropractor|acupuncture|\bpt\b initial|pt progress"
            r"|shock[- ]?wave (therapy|treatment)|functional improvement",
            "5",
        ),
        (
            r"\bpr-?4\b|permanent and stationary|\bp ?& ?s\b|maximum medical improvement"
            r"|\bmmi\b|doctor'?s first report|\bdfr\b|initial.{0,20}consultation",
            "2",
        ),
        # `work status` added 2026-08-21, answered by the reviewers directly: "In the case of Work
        # Status, we will count that as Category 1." It had NO rule, so the cascade re-decided every
        # occurrence and gave four different answers across 89 rows:
        #
        #     category 1 ..... 66 rows, 109 pages   right by luck
        #     category 100 ... 14 rows,  17 pages   dropped - 100 is unchecked for summarization
        #     category 2 ......  8 rows,  15 pages
        #     category 5 ......   1 row,   1 page
        #
        # So this recovers 17 pages and makes the other 72 rows deterministic, which is the point -
        # the same form was being answered four ways.
        #
        # `activity status` added 2026-08-28, reported by Adam after testing a 207-page record.
        # `work status` is a literal token, and Concentra's form is titled "Work Activity Status
        # Report" - the word between defeats it, so the whole family fell through to the cascade:
        #
        #     3 distinct titles, 70 rows, 71 pages, NOT ONE answered by a rule
        #     the cascade's answers: 67 x category 1, 2 x category 2, 1 x category 100
        #
        # The stray 100 is the row Adam reported, and `method` (#189) names the mechanism: that row
        # is `llm-disagree` while the OTHER TWELVE occurrences in the SAME document, on the SAME
        # build, are `llm+embedding` -> 1. One form, two answers, one record, one run - which is
        # what having no rule means, and a 100 is unchecked for summarization so its page reached no
        # deliverable at all.
        #
        # `activity status` rather than `work activity status`: it matches exactly the same 3 titles
        # and 70 rows on the box, so it costs nothing measured, and it also covers "Activity Status
        # and Restrictions Report" - what the source document calls itself in its own Document Type
        # field, and therefore a title segmentation can legitimately produce. "work capacity" and
        # "work ability" are still left out: both appear on ZERO titles, so they stay guesses.
        #
        # Category 1 on the reviewers' own instruction - "In the case of Work Status, we will count
        # that as Category 1" (Adam, 2026-08-24) - and it is where 67 of the 70 already land, so this
        # makes the majority answer deterministic rather than imposing a new one.
        #
        # Rules 12, 13 and 2 all precede this one, so an evaluator's own report, a Permanent and
        # Stationary or a Doctor's First Report still wins if a title ever carries both.
        (
            r"\bpr-?2\b|progress report|progress note|office visit|follow ?-? ?up"
            r"|work status|\bactivity status\b",
            "1",
        ),
        # ORDERS AND PRESCRIPTIONS -> 10, answered by Adam 2026-08-24: "All orders can probably go
        # under the RFA category", and for prescriptions "if they are on their own it's probably fine
        # to put it under the RFA category since it should be the same information being summarized.
        # Often times the same info is in the PR-2 report, but we can still summarize it in case it is
        # not included in the report."
        #
        # PLACEMENT ABOVE IMAGING IS THE POINT, and it is why this is a separate rule rather than
        # tokens added to the `\brfa\b` rule further down. That rule sits BELOW imaging, so "Radiology
        # Order" matched `radiolog` and answered 3 - an order FOR a study classified as the study
        # itself, and then summarized with the diagnostic prompt against a page that contains no
        # findings. Same request-versus-answer confusion that category 15 was created to fix, where 10
        # is the treating physician ASKING and 15 is the determination coming back.
        #
        # PLACEMENT BELOW CATEGORY 1 is deliberate and was raised on review, because this rule and the
        # `work status` token above have never existed in the same file when either was measured.
        # Measured over every title on the box, 1,140 distinct titles across 2,874 rows: 637 carry a
        # category-1 token, 21 carry an order token, and ZERO carry both. So the order decides nothing
        # today and states the intent for the day a title carries both - a progress report, office
        # visit or work status report that mentions ordering an MRI is still that report, because the
        # order is a line inside it, while a standalone order carries no progress-report language at
        # all. The asymmetry is the whole argument: 1 can contain a mention of an order, 10 cannot
        # contain a progress report.
        #
        # NO BARE `order` TOKEN, deliberately. Adam's "all orders" is a statement about treatment
        # orders, and the word is far broader than that on real titles: the box carries "Pre Op
        # Holding Orders" (nursing instructions, answering 100 and 14) and "Outpatient Service Order
        # Information" (100 and 4). Claiming those for RFA would move ward paperwork into a clinical
        # category on a hedge. Named modalities only, so the rule fires on what he was describing.
        #
        # Prescriptions reach no deliverable today: on the two most recent builds every one answered
        # 100 through the cascade (17 rows) and 100 is unchecked for summarization by default, so the
        # reviewer is never offered them. Adam asked for them summarized, which is what 10 gives.
        (
            r"\bprescriptions?\b"
            + r"|\b(?:lab|laboratory|imaging|radiolog\w*|x-? ?ray|mri|ct|ekg|ecg|emg|ncs)\s+orders?\b"
            + r"|\borders?\s+for\s+(?:an?\s+)?(?:lab|laboratory|imaging|radiolog\w*|x-? ?ray|mri|ct)\b",
            "10",
        ),
        # MODALITY terms only. `laborator` used to be here and it defeated D-01/D-02 entirely: that
        # register item made 3 modality-based and 14 specimen-based, and rewrote both taxonomy
        # descriptions so the embedding and LLM stages agree - but rules run FIRST and short-circuit,
        # so every "Laboratory Results ..." title matched here and never reached the fixed stages.
        # Measured 2026-08-13 on a synthetic record: a comprehensive metabolic panel classified 3,
        # unflagged (a rule hit is always high-confidence), and was summarized with the diagnostic
        # prompt at 2.15x the category-14 human median. Deleting the token is enough - 14's own rule
        # below then matches, and imaging keeps priority for mixed titles like "Radiology Test
        # Results" because this rule still precedes it.
        (
            r"\bmri\b|\bct\b|ct scan|x-? ?ray|\bemg\b|\bncs\b|diagnostic study"
            r"|mammogram|sleep study|colonoscopy|dexa|ultrasound|radiolog",
            "3",
        ),
        (
            r"operative report|surgical patholog|patholog|operation performed|oversight physician",
            "8",
        ),
        (r"deposition", "9"),
        (r"\brfa\b|request for authorization", "10"),
        (
            r"adjudication of claim|application for adjudication|compensation claim|\bdwc-? ?1\b",
            "7",
        ),
        (r"comprehensive interval history|medical decision making", "11"),
        (r"gi outpatient|outpatient procedure h ?& ?p", "4"),
        (r"lab(oratory)? results|test results", "14"),
        # LAST in the table on purpose. An emergency-department visit is a treating encounter, and
        # the senior reviewer's rule (2026-08-25) is that "emergency department records in general
        # should be summarized just like a visit would be". But a title can name the department AND
        # a more specific document type - "Emergency Department Radiology Report", "ED Lab Results" -
        # and there the document type is the better answer. Every rule above therefore wins first,
        # and this fires only when a title names an ED visit and nothing more specific.
        #
        # Scoped to a VISIT-shaped noun, because the same reply carves out the intake case: "if it's
        # like a patient demographics emergency intake type record then we don't need to summarize
        # that." So `sign in`, `triage` and `registration` deliberately do not match, and neither
        # does "Emergency Department Encounter Note", which already reaches a shipping category with
        # its content delivered - moving that would be a change with no measured benefit.
        (r"emergency department\s+(?:record|report|visit)|\bed visit record\b", "1"),
        # History & Physical -> a treating report (#161). A pre-operative H&P is the treating
        # physician's own document, `summarize_default` is true for category 1, and 46 of the 53 rows
        # on the box already land there - so the destination is where this form mostly goes already
        # rather than a guess. What made it a defect is that no rule matched it, so the cascade
        # re-decided every occurrence and answered FIVE ways (1, 4, 11, 6, 100), and the one that
        # landed in 100 was dropped. A form answered five ways is wrong whichever answer happens to be
        # right.
        #
        # AFTER the category-4 rule, and that ordering is load-bearing rather than tidiness. That rule
        # matches `outpatient procedure h ?& ?p`, and `\bh ?& ?p\b` here matches the same substring -
        # so placing this any earlier would capture "Outpatient Procedure H&P" and silently break a
        # rule that already exists. Every rule above wins first, which also leaves an evaluator's own
        # report carrying the phrase with 12/13, and a Permanent & Stationary report with 2.
        #
        # `history` is not matched on its own: it appears in "History of Injury", "Past Medical
        # History" and similar, none of which are this document.
        (r"\bhistory (?:and|&) physical\b|\bh ?& ?p\b", "1"),
    )
)


@dataclass
class Classification:
    """Result of classifying one sub-document."""

    category: str
    confidence: str  # "high" | "low"
    method: str
    needs_review: bool


def match_rules(title):
    """Return a category id if a high-precision rule matches the title, else None.

    A bare position-in-care token yields to a document type: "follow-up" says WHEN a visit happened,
    not what the pages are, so "Follow-Up MRI" is an MRI. Withheld only when that token is the sole
    reason category 1 matched, so "Office Visit Follow-Up MRI" is still a visit.

    Document beats wrapper, in two steps. If the title names a document at all (_DOCUMENT_NOUN, e.g.
    "Cover Letter - Psychological Evaluation Report"), the administrative rules stand down entirely
    and the normal cascade answers - falling through to the embedding + LLM stages when no keyword
    rule fits, rather than burying an unrecognised report in General. Otherwise the administrative
    match holds, except where a document-type rule also fired ("Transmittal Letter - MRI Lumbar
    Spine" is an MRI); category 13 does not count there, because it fires on a mere mention of the
    evaluator, which correspondence about an AME contains too.
    """
    text = (title or "").lower()
    matches = [category for pattern, category in _RULES if pattern.search(text)]
    if _EVALUATOR_MENTION in matches and not _EVALUATOR_YIELDS_TO.isdisjoint(matches):
        matches = [c for c in matches if c != _EVALUATOR_MENTION]
    if (
        _FOLLOWUP_MENTION in matches
        and _FOLLOWUP_TOKEN.search(text)
        and not _TREATING_VISIT_WITHOUT_FOLLOWUP.search(text)
        and not _FOLLOWUP_YIELDS_TO.isdisjoint(matches)
    ):
        matches = [c for c in matches if c != _FOLLOWUP_MENTION]
    administrative = any(pattern.search(text) for pattern in _ADMIN_RULES)
    carries_a_document = _DOCUMENT_NOUN.search(text) and not _PAPERWORK_ABOUT_A_DOCUMENT.search(
        text
    )
    if not administrative or carries_a_document:
        return matches[0] if matches else None
    return next((c for c in matches if c != _EVALUATOR_MENTION), DEFAULT_ID)


# --- catalog cache (DB-backed, invalidated on edit) -----------------------------------------
# The classifier's category set comes from the editable DB catalog (auto-assignable only). The
# derived state (catalog text for the LLM, ids, embedding matrix) is cached per process and rebuilt
# when the catalog revision changes. reset_catalog_cache() runs at worker startup. Reads fall back
# to the taxonomy constants when the DB is unavailable (a bare unit test), preserving pre-DB
# behavior.
_catalog_lock = threading.Lock()
_catalog_version_seen = None
_catalog_categories = None
_catalog_text_cache = ""
_category_ids = None
_category_matrix = None

_model = None
# SentenceTransformer.encode is not documented as thread-safe and classify() runs on a thread
# pool, so the encode path is serialized. Encoding is milliseconds; the LLM call dominates.
_embed_lock = threading.Lock()


def reset_catalog_cache():
    """Drop the cached catalog + embedding matrix so the next classify reloads from the DB."""
    global _catalog_version_seen, _catalog_categories, _catalog_text_cache
    global _category_ids, _category_matrix
    with _catalog_lock:
        _catalog_version_seen = None
        _catalog_categories = None
        _catalog_text_cache = ""
        _category_ids = None
        _category_matrix = None


def _catalog_version():
    """Current catalog revision, or -1 when the DB is unavailable (constants fallback)."""
    try:
        with get_sessionmaker()() as session:
            return catalog.catalog_version(session)
    except Exception:
        return -1


def _auto_assign_categories():
    """Auto-assignable categories as dicts; taxonomy constants when the DB is unavailable."""
    try:
        with get_sessionmaker()() as session:
            rows = catalog.get_categories(session, auto_assign=True)
        if rows:
            return rows
    except Exception:
        pass
    return [
        {"id": c.id, "name": c.name, "description": c.description, "examples": list(c.examples)}
        for c in CATEGORIES.values()
    ]


def _corpus(category):
    """Representative text for a category dict (mirrors taxonomy.Category.corpus)."""
    examples = category.get("examples") or []
    return f"{category['name']}. {category['description']} Examples: " + "; ".join(examples)


def _refresh_locked():
    """Reload the catalog if its revision changed. Caller must hold ``_catalog_lock``."""
    global _catalog_version_seen, _catalog_categories, _catalog_text_cache
    global _category_ids, _category_matrix
    version = _catalog_version()
    if version != _catalog_version_seen or _catalog_categories is None:
        categories = _auto_assign_categories()
        _catalog_categories = categories
        _catalog_text_cache = "\n".join(
            f"- {c['id']}: {c['name']} - {c['description']}" for c in categories
        )
        _category_ids = None  # force the embedding matrix to rebuild for the new set
        _category_matrix = None
        _catalog_version_seen = version


def _catalog_text():
    with _catalog_lock:
        _refresh_locked()
        return _catalog_text_cache


def _allowed_ids():
    with _catalog_lock:
        _refresh_locked()
        return [c["id"] for c in _catalog_categories]


def _encode(texts):
    """Encode texts into L2-normalized vectors using the local sentence-transformers model."""
    global _model
    with _embed_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(_EMBED_MODEL_NAME)
        return np.asarray(_model.encode(list(texts), normalize_embeddings=True))


def _category_vectors():
    """Return (ids, matrix) of encoded category corpora, rebuilt when the catalog changes."""
    global _category_ids, _category_matrix
    with _catalog_lock:
        _refresh_locked()
        if _category_matrix is None:
            _category_ids = [c["id"] for c in _catalog_categories]
            _category_matrix = _encode([_corpus(c) for c in _catalog_categories])
        return _category_ids, _category_matrix


def embed_classify(text):
    """Return (category_id, cosine_score) for the nearest category by embedding."""
    ids, matrix = _category_vectors()
    vec = _encode([text])[0]
    sims = matrix @ vec  # both sides are L2-normalized, so this is cosine similarity
    best = int(np.argmax(sims))
    return ids[best], float(sims[best])


def llm_classify(text, model=None):
    """Classify via Gemini constrained-enum output; returns a valid id or None on failure.

    Defaults to settings.classify_model (the cheapest tier - this is a short, structured enum task).
    ``model`` is overridable so an A/B can compare tiers on identical inputs.
    """
    allowed = _allowed_ids()
    prompt = (
        "Classify the medical-record document below into exactly one category id from this "
        "list. Choose 100 only if none of the specific categories fit.\n"
        "Administrative and correspondence documents - routing slips, cover letters, emails and "
        "faxes, legal declarations, proofs of service, records requests and record indexes - are "
        "100 even when they mention a QME/AME or another document type, because they accompany "
        "that document rather than being it.\n\n"
        f"{_catalog_text()}\n\nDocument:\n{text}\n\nReturn only the category id."
    )
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": list(allowed)},
        system_instruction=(
            "You classify California workers'-compensation medical-record document types. "
            "Return exactly one category id from the allowed set."
        ),
    )
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model or get_settings().classify_model,
            contents=prompt,
            config=config,
        )
    except JobCancelled:
        # NOT a model failure - the reviewer pressed Stop. `generate_with_retry` raises this out of
        # its backoff sleep as a cooperative control-flow signal, meant to unwind through the pool to
        # _run's handler. Catching it here logged a deliberate user action as an LLM error, which is
        # misleading in exactly the place an operator looks to ask whether Vertex was rejecting
        # calls, and returned None - so classify() took the llm_category-is-None branch and answered
        # from the embedding alone. Bounded (report() catches the cancel a row later) but wrong on
        # both counts: up to CLASSIFY_WORKERS rows decided by a single weak vote.
        raise
    except Exception as exc:
        logger.warning("LLM classification failed: %s", exc)
        return None
    category = (response.text or "").strip()
    return category if category in set(allowed) else None


def classify(title, page_text=None):
    """Classify a sub-document, cross-checking the embedding and LLM votes.

    Rules win outright when they fire. Otherwise the embedding and LLM must agree to be confident;
    disagreement (or an unavailable LLM) assigns a best guess and sets ``needs_review``.
    """
    title = (title or "").strip()
    text = (page_text or title).strip()

    rule_category = match_rules(title)
    if rule_category:
        return Classification(rule_category, "high", "rules", needs_review=False)

    if not text:
        return Classification(DEFAULT_ID, "low", "empty", needs_review=True)

    try:
        embed_category, _score = embed_classify(text)
    except Exception as exc:
        logger.warning("embedding classification failed: %s", exc)
        embed_category = None
    llm_category = llm_classify(text)

    if embed_category is None and llm_category is None:
        return Classification(DEFAULT_ID, "low", "no-signal", needs_review=True)
    if llm_category is None:
        return Classification(embed_category, "low", "embedding-only", needs_review=True)
    if embed_category is None:
        return Classification(llm_category, "low", "llm-only", needs_review=True)
    if llm_category == embed_category:
        return Classification(llm_category, "high", "llm+embedding", needs_review=False)
    return Classification(llm_category, "low", "llm-disagree", needs_review=True)
