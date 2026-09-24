"""Per-document summarization for the review flow.

Reproduces the legacy per-row behavior (same category prompts, title extraction, decorations).
Callers pass rows + the resolved prompt explicitly, so this service stays DB-free; when the
prompt is omitted it falls back to the hardcoded prompts.py dict (category_11 has none -> the
general prompt, avoiding the historical KeyError).

Model calls go through services.llm, so which vendor answers is a config value rather than an
import. This module no longer names an SDK.
"""

import difflib
import logging
import re

from app.config import get_settings
from app.errors import EmptyExtractionError, is_rate_limited
from app.services.deposition_pages import transcript_page_offset
from app.services.house_style import one_paragraph, sentence_case_caps_runs
from app.services.llm import TextPart, get_provider
from app.services.ocr import extract_pages_with_report
from app.services.prompts import prompts
from app.services.provenance import fingerprint, summary_prompt_fingerprint
from app.services.rasterise import page_image_parts
from app.services.summary_verify import VERIFY_PROMPT, verify_summary

logger = logging.getLogger(__name__)

# The header line a human reviewer writes, measured across 812 of 813 entries in ten completed MRR
# deliverables: ALL CAPS, "AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.". The old prompt inverted
# that order, had no facility slot (so the model put letterhead in the title instead), mandated
# dashes, and banned the comma the credential form needs. Diagnostics get an explicit naming form
# because ~18 of 36 measured category-3 titles named the document class ("Radiology Report") rather
# than the study. The date is NOT here: it is already its own column, as in the human layout.
TITLE_PROMPT = (
    "You extract the header line for ONE medical sub-document. Return exactly one line, in this "
    "order, with the elements separated by a period and a space, and the WHOLE line in capital "
    "letters:\n\n"
    "AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.\n\n"
    "1. AUTHOR - the person who conducted and signed THIS encounter, read from the signature "
    "block where there is one. Never the referring provider. Write the name, then a comma, "
    'then the credentials with periods, for example "JANE SMITH, M.D." Non-physicians are '
    "included: M.D., D.O., D.C., P.T., R.N., P.A., N.P., PSY.D., L.V.N., O.D., D.D.S.\n"
    "2. FACILITY - the clinic, imaging centre, hospital, laboratory, or practice that produced "
    "the document, read from the LETTERHEAD at the top of the page. The facility's NAME only: "
    "never its street address, suite, city, state, ZIP code, phone or fax number, even though "
    "the letterhead prints them beside the name.\n"
    "   A stamp added to the photocopy afterwards is NOT the facility. Records-copying, "
    "transcription, billing and bill-review vendors stamp their name, an address and a "
    "received-date or bill/DCN number onto the page; that names who handled the paper, not who "
    "saw the patient. Where such a stamp carries the facility's own name with a department "
    'beside it, keep the facility and drop the department - "VALLEY OCCUPATIONAL HEALTH", never '
    '"VALLEY OCCUPATIONAL HEALTH TRANSCRIPTION".\n'
    "3. DOCUMENT TYPE - what the document IS.\n"
    "   For a diagnostic study, name the study, never its class. Use the form "
    "<MODALITY> OF THE <SIDE IF STATED> <BODY PART> <CONTRAST STATUS IF STATED>, for example "
    '"MRI OF THE CERVICAL SPINE WITHOUT CONTRAST", "CT OF THE HEAD WITHOUT CONTRAST", '
    '"X-RAY OF THE LEFT WRIST", "EMG/NCS OF THE UPPER EXTREMITIES". Never "RADIOLOGY REPORT", '
    '"GENERAL RADIOLOGY PROCEDURE", or "DIAGNOSTIC REPORT". Ultrasound and mammogram studies '
    'are named organ-first, for example "THYROID ULTRASOUND".\n'
    "   A DEPOSITION or recorded statement is titled for the person being DEPOSED and for nobody "
    'else: write the whole line as "DEPOSITION OF <NAME>" and omit the AUTHOR and FACILITY '
    "elements entirely. The deponent is the witness who is sworn and answers the questions, named "
    "in the caption or at the start of the examination. The certified shorthand reporter who "
    "signed the transcript is NOT the author, and the court reporting service on the cover page "
    "is NOT the facility: both appear on every deposition, so taking them makes every deposition "
    "title look alike and tells a reviewer nothing about whose testimony it is.\n"
    "   A facility name is NOT a document type. If the top of the page carries only letterhead, "
    "read on for the study or report heading, which often sits above the findings.\n\n"
    "If an element is not stated in the document, omit that element and its separator. Do not "
    'write "UNKNOWN", "UNSPECIFIED", or any placeholder - an absent element is left out, the same '
    "way an absent point is left out of the summary body.\n\n"
    "Never include page numbers or page ranges, dates, or the patient's name. Return only the "
    "line, with no commentary.\n\n"
    # A hint, not a guarantee: the title call has no response_schema, and Gemini does not support
    # maxLength on strings (only enum and format), so a declared bound would be silently ignored.
    # _usable_title is what actually enforces this. Stated here because on 2026-08-14 one row got
    # ~620 characters of prose back, which is what a limit in the prompt discourages.
    "HARD LIMIT: at most 150 characters. One line. Never a sentence, never a paragraph, never an "
    "explanation of what the document contains."
)

# The shared rules, as BLOCKS rather than one string, because they are not all universal. Assembled
# per category by build_preamble: the block reached 4,927 characters, which is 81% of the system
# message for a one-line laboratory summary - so category 14 was being instructed about depositions,
# embedded records reviews, range of motion and pain scales, none of which can apply to it.
#
# Each block states its WHY so it survives edits. Two blocks were reworded to stand ALONE when this
# was split: the diagnostic-verdict rule used to open "That rule does NOT apply..." (referring to the
# normal-findings rule) and the deposition rule used to open "The single-paragraph rule does NOT
# apply...". Under per-category assembly those references can be dropped from the message, leaving a
# dangling "that rule" - so both now say what they mean without pointing at a neighbour.
_FACTUALITY = (
    "CRITICAL FACTUALITY RULES (a medical-legal report depends on these):\n"
    "- Use ONLY information explicitly stated in the text below. Do NOT infer, assume, "
    "extrapolate, or add anything not written - inference is how errors enter the record.\n"
    "- If a detail is absent, OMIT it. Never guess or fill a gap, and never write a point then "
    "say 'not specified'.\n"
    "- Copy dates, percentages, measurements, ratings, and medication names/doses EXACTLY as "
    "written; do not round, convert, or paraphrase a number.\n"
    "- Do NOT contradict yourself: every statement must be consistent with the source and with "
    "your other statements.\n"
    "- If the text is illegible, ambiguous, or internally contradictory, omit that point rather "
    "than resolving it by guessing.\n\n"
)

# Content scope lives here, once, so a category prompt only has to name its own points.
# Measured against 55 eData deliverables (2115 entries): the length gap is per-category, not uniform -
# labs 25x, diagnostic studies 4.3x, therapy notes 3.3x, treating reports 2.4x, while medico-legal
# evaluations and depositions run SHORTER than the human convention. A corpus-wide median must not be
# used as a target: a quarter of the corpus is labs, forms and one-line impressions.
#
# The employer/occupation carve-out was removed on measured evidence: the human corpus confines both
# to WCAB filings (51%/39%) and comprehensive evaluations (40%/25%), and uses them in 1% of treating
# notes and 0% of imaging, therapy and lab entries. Categories 2 and 7 name them directly.
_CONTENT_HEADER = "CONTENT RULES (what belongs in the summary):\n"

_C_POINT_SCOPE = (
    "- Include a point ONLY if the category rules below name it for this document type. Do not "
    "add a point the rules do not list, however relevant it looks - unrequested detail is the "
    "main reason summaries run long.\n"
)

# Widened 2026-07-31 on tester feedback. The four banned words ("normal, negative, unremarkable,
# within normal limits") missed three things a reviewer counts as the same noise, both current-build
# examples being exactly these archetypes: "He denied anterior pressure, chest tightness, fever, or
# chills" and "**Physical Exam**: The genitalia/rectal exam was refused". An absence is not a normal
# finding, so the old wording was literally silent on it.
#
# The second paragraph is load-bearing, not restatement: build_preamble sends this block and _C_VERDICT
# to disjoint category sets EXCEPT for an unknown id, which receives BOTH. Without the carve-out a new
# category would be told to omit inconclusive results by one block and to report them by the next, and
# emptying out imaging summaries is the exact regression PR #55 was written to fix.
_C_NORMAL_FINDINGS = (
    "- Report positive and abnormal findings only when describing an examination, a history, or a "
    "clinical assessment. Omit anything recorded as normal, negative, unremarkable, or within "
    "normal limits; a reader assumes anything not mentioned was normal. The same applies to three "
    'things that are not findings at all: an explicit ABSENCE stated against a point ("no known '
    'allergies", "denies fever or chills") - carry such a point only when there is something to '
    "report; a test, examination, or treatment that was REFUSED, declined, deferred, or not "
    "performed; and a result the document itself calls INCONCLUSIVE or non-diagnostic.\n"
    "- That omission governs examinations, histories, and assessments ONLY. Where another rule asks "
    "for the impression, result, or verdict of a diagnostic study or a laboratory or test result, "
    "that rule wins and the verdict is reported as written, even when it is normal, negative, or "
    "inconclusive.\n"
)

# Stands alone deliberately: it used to open "That rule does NOT apply...", which dangles once the
# normal-findings rule is not sent to this category.
_C_VERDICT = (
    "- For the conclusion of a diagnostic study or a laboratory or test result, report the "
    "impression, result, or verdict exactly as stated EVEN WHEN it is normal, negative, or "
    "unremarkable. For those documents the verdict IS the content, and omitting it leaves the "
    "summary empty.\n"
)

_C_EMBEDDED_REVIEW = (
    "- If the document contains a review of earlier medical records inside it, record that the "
    "review is present and take from it only the diagnostic studies it reports. Never summarize "
    "the embedded review in whole - it restates records that are summarized in their own right "
    "elsewhere in the set.\n"
)

_C_CODES = "- Do NOT write ICD, CPT, or other billing codes, even when the document lists them.\n"

# Height and weight ONLY, deliberately. Measured on the 55-deliverable human corpus (2115 entries): a
# stated height appears once and a weight 8 times, so omitting them is safe. Adrian scoped this to
# those two on 2026-07-30 and reserved the other vitals for a later call, so the rule must not creep:
# blood pressure, pulse, respiration, temperature and oxygen saturation stay at the model's
# discretion. BMI is excluded outright - it appears 52 times in that corpus and EVERY occurrence is a
# numbered DIAGNOSIS ("6. BMI 42.5, severe obesity equivalent"), so a rule that swept it up would
# start deleting diagnoses.
_C_VITALS = (
    "- Do NOT report the patient's height or weight. They are recorded at nearly every encounter and "
    "belong in none of these summaries. This covers height and weight ONLY: other vital signs are "
    "left to your judgement, and BMI is not restricted - where the document states a BMI as a "
    "diagnosis, keep it.\n"
)

_C_PAIN = (
    "- For pain, give frequency, intensity on the scale the document uses, and location, and "
    "nothing else. Do not add qualitative descriptors, and never state intensity twice - write "
    '"6/10", not "moderate 6/10". The common failure is a list of quality words in front of the '
    'word pain: write "constant right wrist pain rated 6/10", NEVER "frequent, sharp, stabbing, '
    'aching, dull pain rated 6/10". Keep frequency (constant, intermittent, occasional, frequent); '
    "drop quality (sharp, dull, aching, stabbing, throbbing, burning, cramping, shooting).\n"
)

# The human corpus describes range of motion qualitatively 473 times and quotes degrees only 31 times,
# so a bare measurement is not what a reader expects. The reference-range fallback is a DELIBERATE and
# narrowly-bounded exception to the no-inference rule (Adrian's call, 2026-07-30): normal joint ranges
# are textbook reference values, not a claim about this patient. It must stay the only exception, and
# it must never change the measured number.
_C_RANGE_OF_MOTION = (
    "- Range of motion: a bare measurement does not tell the reader whether the joint moves. Say "
    "whether it is reduced. Use the document's own word when it gives one (decreased, limited, "
    "restricted, within normal limits). When the document prints a normal value beside the "
    'measurement ("flexion 40/60"), state the comparison from those two numbers. Only when the '
    "document gives neither, compare the measurement against the standard normal range for that "
    "joint and motion and say whether it is reduced, normal, or increased. Reference ranges for "
    "joints are the ONE exception to the no-inference rule above, because they are textbook values "
    "rather than a statement about this patient; keep the measured value exactly as written "
    "either way, and never replace a number with a word.\n"
)

_FORMAT_HEADER = (
    "FORMATTING (STRICT - overrides any layout instruction in the category rules below):\n"
)

_F_ONE_PARAGRAPH = (
    "- Write the ENTIRE summary as ONE continuous paragraph. Do NOT use line breaks, blank lines, "
    "bullet points, or numbered lists to separate points; when the rules below organize the content "
    "into named points or sections, run those points together inline in one single paragraph.\n"
)

# Depositions are the one exception to the single-paragraph rule: a transcript is summarized in page
# groups, so collapsing it into one paragraph would destroy the format rather than tidy it. Stands
# alone - it used to be phrased as an exception to the paragraph rule, which is not sent here at all.
#
# THE HUMAN CONVENTION IS ONE PAGE PER PARAGRAPH, NOT THREE. Measured twice: the median gap between
# referenced pages is 1 (978 transitions), and re-measured 2026-08-06 across all 55 converted human
# deliverables, 941 of 1,276 summary lines open with "On page N, lines A to B" while exactly ONE cites
# a page RANGE. Three-page grouping is Adrian's instruction (2026-08-06), made with that measurement
# in front of him. Recorded here so nobody later reads the divergence as a defect and "fixes" it back.
_F_DEPOSITION = (
    "- Summarize this transcript in GROUPS OF THREE consecutive pages, one paragraph per group, each "
    "beginning with the range of pages it covers. Do NOT merge the groups into one paragraph and do "
    "NOT write one paragraph per page: the grouping is what a reader relies on to locate testimony.\n"
)

_F_BOLD = (
    "- Bold ONLY the short point/section labels, e.g. **Subjective Complaints**, **Diagnoses**, "
    "**Work Status**. Do NOT bold the text that follows a label, and NEVER bold a whole sentence, a "
    "whole point, or the entire summary - bolding everything makes the emphasis meaningless.\n"
)

# The two house rules in summary_verify that are CORRECTION-ONLY: rule 3 (capitalisation) and rule 4
# (range of motion). Neither can ever justify a bold point heading ceasing to exist, which is what
# makes them the exact set the guard below acts on.
#
# `vitals` and `pain_descriptor` are deliberately NOT here even though they read as equally cosmetic:
# house rule 1 removes height and weight and rule 2 removes pain quality words, so either can
# legitimately empty a point and take its heading with it. Blocking those would suppress a correct fix.
_CORRECTION_ONLY_ISSUES = frozenset({"capitalization", "range_of_motion"})

# The audit's name for a claim the source does not support. Named rather than inlined because the
# ceiling and any later guard must exempt the SAME type - a second spelling here is a fabrication
# fix silently blocked.
_FABRICATION_ISSUE = "unsupported"


def _bold_span_count(text: str) -> int:
    """How many `**...**` spans a body carries.

    Spans, not `**` occurrences: an unbalanced marker would otherwise inflate the count and make a
    heading look present when it is not.
    """
    return len(re.findall(r"\*\*(.+?)\*\*", text or ""))


def _drops_required_headings(raw: str, fixed: str, issue_types: set[str]) -> bool:
    """True when the audit removed a bold point heading for a reason that cannot justify removing one.

    Measured 2026-07-31 on the current build: 7 of 16 audited summaries lost bold headings and 5 lost
    every one, against 7.3% before the house rules landed. One row was generated correctly as
    `**Body part being treated**: ...` and the audit rewrote it to bare prose, storing its own reason
    as "Summary contains capitalized headers" - it read the required structure as a stray capitalised
    header. The prompt now says otherwise, but a prompt is a request; this is the guarantee.

    Compares COUNTS, never heading text, and deliberately so: renaming or re-casing a heading is the
    behaviour the audit is being asked for, and comparing text would block exactly that. Only a
    heading that stopped existing is the defect.

    A SUBSTANTIVE reason - an unsupported claim, a vitals line - still justifies a rewrite, and one
    of those can legitimately empty a point and take its heading with it. It cannot empty MOST of
    them. Measured 2026-09-18 on a self-hosted model: a body carrying eight bold points was answered
    with a single line naming the date, the physician and the facility - 1,482 chars to 126, every
    clinical finding gone - because one `vitals` issue put the set outside the correction-only pair
    and the guard below returned False without looking at how much was removed. Blast radius of the
    ceiling, replayed over every stored rewrite: 0 of 475 on gemini, so nothing about the answering
    model's current behaviour changes; 14 of 929 pre-provenance rows, which lost EVERY heading while
    keeping 90-98% of their prose, i.e. exactly the de-bolding this guard was written to reject.

    THE TRADE, named here so the next reader does not have to rediscover it: the ceiling also
    catches `unsupported`, which means a FABRICATED claim. Where a summary is largely fabricated,
    the audit's fix legitimately guts it, this rejects that fix, and the fabricated text is what
    stands. It is flagged and logged rather than silent, and the replay above found 0 of 475 on the
    answering model in production - so it is forward risk on the self-hosted pipeline rather than a
    live regression. The alternative trade is worse: accepting every gutting rewrite is what the
    reviewer reported, and a body reduced to one line loses content that WAS supported.
    """
    if not issue_types:
        return False
    raw_headings = _bold_span_count(raw)
    if issue_types <= _CORRECTION_ONLY_ISSUES:
        return _bold_span_count(fixed) < raw_headings
    # A FABRICATED CLAIM IS NEVER BLOCKED. `unsupported` means the audit found something the source
    # does not support, and where a summary is largely fabricated its fix legitimately guts the
    # body - so the ceiling below would reject that fix and leave the fabricated text standing.
    # Narrows what the ceiling covers, deliberately, because shipping invented clinical content is
    # the worse of the two failures.
    #
    # WIDER THAN THAT SENTENCE, and deliberately: this fires when `unsupported` appears AT ALL,
    # including alongside other issues where the rewrite still deletes everything. The narrow
    # reading - a rewrite ANSWERING fabrication - would need the audit to report its issues in
    # priority order, and it reports a set. Given the set, the choice is between letting one
    # fabrication finding exempt the whole rewrite and blocking a fabrication fix whenever it
    # arrives with company; the first ships less invented content, which is the failure that
    # matters here. Pinned by the mixed-issue case in
    # `test_a_fabrication_fix_is_never_blocked_however_much_it_removes`.
    #
    # It does NOT reopen the case this guard was written for: that row carried a `vitals` issue,
    # not `unsupported`, so it is still rejected. Pinned by a test either side of the exemption.
    if _FABRICATION_ISSUE in issue_types:
        return False
    # The ceiling. Three is the floor for "structured": below it, losing one heading IS losing a
    # large share, and a vitals fix on a two-point body is the case the exclusion above protects.
    return raw_headings >= 3 and _bold_span_count(fixed) * 2 < raw_headings


# Content words only. Case, punctuation and the `**` of a heading are all things the audit is
# legitimately allowed to change, so none of them can form part of the test below.
_CONTENT_WORD = re.compile(r"[a-z0-9]+")


def _restates_the_title(fixed: str, title: str) -> bool:
    """True when the audit's "correction" says nothing the row's own title had not already said.

    A reviewer reported summaries that read as the document title and nothing else, while the
    same document type summarized correctly elsewhere in the SAME record. The reported body was
    its own row title with the capitals folded: `..., D.c. ...` where the title reads
    `..., D.C. ...`. (Redacted here because a title names the provider.)

    That lower-case `c` is the provenance, and it is why this guard sits on the AUDIT
    rather than on generation: it is precisely what `sentence_case_caps_runs` returns for the ALL
    CAPS title, and the only call that case-folds an audit reply is the one building
    `verified_text`. A raw body is case-folded before the audit ever sees it, so a title-shaped
    RAW body could not arrive in that exact form. The body was a real summary and the audit
    replaced it with the header.

    Compares CONTENT WORDS as a subset rather than lengths, deliberately. A length ratio needs a
    threshold and the only honest way to choose one is to measure the distribution, which needs a
    box this was written without. A subset test needs no threshold and cannot reject a real
    correction: for it to fire, every word of the rewrite must already appear in the title, which
    is the definition of a body that states nothing. An empty rewrite falls out of the same test,
    the empty set being a subset of anything.
    """
    if not title:
        # Nothing to be a restatement OF. Firing here would flag every row whose title call came
        # back empty - a different fault, with its own handling in `_usable_title`.
        return False
    return set(_CONTENT_WORD.findall((fixed or "").lower())) <= set(
        _CONTENT_WORD.findall(title.lower())
    )


# "On pages 4 to 6," / "On pages 34 and 35," - the opener every deposition paragraph carries. Matched
# loosely (any leading whitespace, either joiner, optional comma) because the guard's job is to notice
# that citations STOPPED EXISTING, not to police their punctuation.
_PAGE_RANGE_OPENER = re.compile(r"^\s*On pages?\s+\d+\s*(?:to|and|-)\s*\d+", re.IGNORECASE | re.M)


def _drops_deposition_structure(raw: str, fixed: str) -> bool:
    """True when the audit collapsed a deposition's page grouping or dropped its page citations.

    A deposition is the one category whose body is deliberately MANY paragraphs, each opening with the
    transcript pages it covers. That structure is the entire point - it is how a reviewer finds the
    testimony a paragraph came from - and the audit rewrites the whole body, so it is one model call
    away from being flattened into prose.

    Mirrors ``_drops_required_headings``: compare COUNTS, never text. Rewording a paragraph or
    correcting a date inside one is exactly what the audit is for; a paragraph or a citation that
    stopped existing is the defect. Unlike that guard this does NOT restrict itself to the
    correction-only issue types, because no faithfulness finding justifies deleting a page reference -
    the reference is not a claim about the medicine, it is a pointer to the source.
    """
    raw_paragraphs = [p for p in raw.split("\n") if p.strip()]
    fixed_paragraphs = [p for p in fixed.split("\n") if p.strip()]
    if len(fixed_paragraphs) < len(raw_paragraphs):
        return True
    return len(_PAGE_RANGE_OPENER.findall(fixed)) < len(_PAGE_RANGE_OPENER.findall(raw))


# Forms print employer, occupation and headings in capitals, and the model was copying that through:
# no all-caps run of three or more words appears in the body of any of the 2115 measured human
# entries. Acronyms are exempted explicitly, or the rule turns MRI into "Mri". The header line above
# the body is a separate artefact and IS all caps by convention (812 of 813 entries), which is why
# this rule names the summary body. house_style.sentence_case_caps_runs enforces the same thing
# deterministically afterwards; this block still earns its place by stopping the model producing it.
_F_SENTENCE_CASE = (
    "- Write the summary body in ordinary sentence case. Do NOT write any word, sentence, line, or "
    "point in capital letters, even where the document does. Put a company or facility name in "
    'title case ("Cedar Ridge Logistics, Inc.", not "CEDAR RIDGE LOGISTICS, INC"), an occupation in '
    'sentence case ("General laborer", not "GENERAL LABORER"), and a heading you carry over in '
    "sentence case. Genuine acronyms and initialisms are the exception and stay as written: MRI, CT, "
    "EMG, NCS, ECG, QME, AME, PR-2, PR-4, RFA, ADL, TTD, WPI, MMI, HPI, PE, ROM, ICD, CPT.\n"
)

# Categories whose documents describe a physical examination, and so can carry normal findings, a
# height and weight, a pain rating and a range of motion.
_EXAM_CATEGORIES = frozenset({"1", "2", "5", "6", "12", "13"})
# Categories whose whole content IS a verdict, where a normal result must still be reported.
_VERDICT_CATEGORIES = frozenset({"3", "14"})
# Depositions and recorded statements: page-per-line, never one paragraph.
_DEPOSITION_CATEGORIES = frozenset({"9"})
# Only categories 12 and 13 (QME/AME supplementals and evaluations) mention an embedded records
# review - verified by scanning all 14 category prompts - so only they carry that rule and only they
# can act on a list of the record's other studies.
_EMBEDDED_REVIEW_CATEGORIES = frozenset({"12", "13"})
# The catch-all. Its ID is known; its CONTENT is not, and that is the distinction this exists to
# draw (#216). Every other known id earns a reduced preamble because its documents structurally
# cannot contain the withheld thing - a Request For Authorization has no range of motion. 100 holds
# whatever nothing else claimed, so nothing can be said about what its documents contain.
#
# And the population that REACHES the summarizer at 100 is selected for being clinical. 100 seeds
# `summarize_default = False`, so a row there is unchecked unless a reviewer deliberately ticks it -
# which they do when they judge the content worth summarizing. Measured 2026-09-02: 16 of 1,834
# rows at 100 are ticked (0.9%), and 141 summaries have been produced at this category. So the one
# population guaranteed to arrive with clinical content was the one guaranteed to receive none of
# the clinical instructions.
#
# Treated as an unrecognised id for the blocks that describe CONTENT, which is what the
# default-INCLUDE policy in `build_preamble` was written for - but NOT for the three measurement
# blocks. Instructing the catch-all about vitals, pain scales and range of motion is a scope change
# that belongs to #216, so the flag is split rather than the question quietly answered here.
_CATCH_ALL_CATEGORIES = frozenset({"100"})

# Every id the catalog ships. An id outside this set gets EVERY block (see build_preamble).
_KNOWN_CATEGORIES = (
    _EXAM_CATEGORIES
    | _VERDICT_CATEGORIES
    | _DEPOSITION_CATEGORIES
    | _EMBEDDED_REVIEW_CATEGORIES
    # 15 (UR/IMR determinations) is listed here rather than in _VERDICT_CATEGORIES on purpose. Its
    # content IS a verdict, so that block looks like the obvious home - but _C_VERDICT is worded for
    # "a diagnostic study or a laboratory or test result", which a determination letter is not, and
    # the requirement is stated directly in category 15's own prompt instead. Listing it here gives
    # it the same minimal preamble as 10, the request this category answers.
    #
    # 16 (hospital discharge summaries) is here for the same reason and needs the argument stated,
    # because the block it MUST NOT have looks like the one it obviously should. Its content is the
    # patient's condition at discharge, so `_C_NORMAL_FINDINGS` fires on it as "a clinical
    # assessment" and instructs the model to omit anything "recorded as normal, negative,
    # unremarkable, or within normal limits" - which is most of what a discharge summary says, and
    # exactly the content the reviewers asked this category for. That block's only carve-out is the
    # verdict of a diagnostic study or a laboratory result, which a discharge summary is not, so
    # nothing rescues it. `_C_VERDICT` is ruled out on the same wording. The requirement is stated
    # in category 16's own prompt instead, as category 15 states its determination requirement.
    | frozenset({"4", "7", "8", "10", "11", "15", "16", "100"})
)


def build_preamble(category) -> str:
    """The shared rules that can bind on THIS category, assembled in a fixed order.

    Default is INCLUDE: an id the catalog does not ship yet (an admin can create one at any time via
    POST /admin/categories) receives every block except the deposition format, so a new category is
    never silently under-instructed. Only a KNOWN id has blocks withheld, and only where its documents
    structurally cannot contain the thing - a laboratory result has no range of motion, and a
    deposition transcript is not written as one paragraph.

    THE CATCH-ALL TAKES THE DEFAULT TOO (#216). Withholding requires knowing what the documents
    contain, and 100 is the one category about which nothing can be said - see
    `_CATCH_ALL_CATEGORIES`. It is not a special case bolted on; it is the same situation as an
    unrecognised id, so `content_unknown` covers both and the rest of the function is untouched.

    THE `exam` FLAG IS SPLIT IN TWO, which #216 asks for and which is what keeps the catch-all's
    share of this narrow. One flag used to gate both `_C_NORMAL_FINDINGS` and, later,
    `_C_VITALS`/`_C_PAIN`/`_C_RANGE_OF_MOTION`, so 100 could not be given the first without the
    other three - and instructing the catch-all about vitals, pain scales and range of motion is a
    scope change rather than a defect fix. Split, the catch-all takes what it plainly needs (a
    normal finding is content, and dropping it is content lost) and the scope question does not
    arise. `_EXAM_CATEGORIES` still drives both, so no other category moves.

    So `build_preamble("100")` is deliberately NOT identical to an unrecognised id's. They differ by
    exactly the three measurement blocks, and that difference is the open question left on #216 -
    an unknown id is default-INCLUDE by policy, while 100 is a known id whose content is unknown and
    whose measurement instructions nobody has decided on.
    """
    cat = str(category)
    unknown = cat not in _KNOWN_CATEGORIES
    content_unknown = unknown or cat in _CATCH_ALL_CATEGORIES
    deposition = cat in _DEPOSITION_CATEGORIES
    findings = content_unknown or cat in _EXAM_CATEGORIES
    measurements = unknown or cat in _EXAM_CATEGORIES
    verdict = content_unknown or cat in _VERDICT_CATEGORIES
    embedded = content_unknown or cat in _EMBEDDED_REVIEW_CATEGORIES

    parts = [_FACTUALITY, _CONTENT_HEADER, _C_POINT_SCOPE]
    if findings:
        parts.append(_C_NORMAL_FINDINGS)
    if verdict:
        parts.append(_C_VERDICT)
    if embedded:
        parts.append(_C_EMBEDDED_REVIEW)
    parts.append(_C_CODES)
    if measurements:
        parts += [_C_VITALS, _C_PAIN, _C_RANGE_OF_MOTION]
    parts += ["\n", _FORMAT_HEADER]
    parts.append(_F_DEPOSITION if deposition else _F_ONE_PARAGRAPH)
    parts += [_F_BOLD, _F_SENTENCE_CASE, "\n"]
    return "".join(parts)


# Categories 1 and 2 are follow-up and comprehensive treating reports, the two that recount an
# earlier visit before reporting the current one. The recap is not wrong, it is redundant: the
# earlier visit has its own sub-document in the same record and is summarized there in its own
# right - the same argument as the embedded-review rule above. Confined to these two because a
# medico-legal evaluation (12, 13) is REQUIRED to carry the injury history.
_CURRENT_VISIT_CATEGORIES = frozenset({"1", "2"})


def _document_date_block(document_date) -> str:
    """The system-message block telling the model which encounter this document IS.

    Segmentation already extracts the document's date per row, so handing it over is free and far
    more reliable than asking the model to work out which of the dates in the text is the document's
    own. Returns "" when the date is missing or the "-" sentinel, so the caller sends the system
    message unchanged rather than an empty assertion the model has to interpret.
    """
    date = str(document_date or "").strip()
    if not date or date == "-":
        return ""
    return (
        f"\n\nTHIS DOCUMENT IS DATED {date}, AND IT RECORDS THAT ENCOUNTER.\n"
        "Where the text recounts an EARLIER visit - its complaints, its examination findings, its "
        "treatment, or its work status - and attributes them to an earlier date, that is a recap. "
        "Do NOT summarize it: that visit has its own document in this record and is summarized "
        f"there. Summarize the findings, complaints, treatment and work status of {date} only. The "
        "mechanism of injury and the injury history stay where the category rules ask for them, "
        "stated once; what you leave out is the previous visit's own findings."
    )


def standalone_studies_from_rows(rows, exclude=None) -> list[dict]:
    """The record's standalone diagnostic studies as ``[{title, date}]``, excluding one row.

    ``rows`` are the row dicts that WILL be summarized: a study the reviewer unchecked is not
    summarized anywhere, so suppressing it from the embedded review too would drop it from the
    record entirely. ``exclude`` is the row being summarized, matched on its page range, so a
    category-3 row is never listed against itself.
    """
    skip = None if exclude is None else (int(exclude["start"]), int(exclude["end"]))
    studies = []
    for row in rows:
        if str(row["category"]) != "3":
            continue
        if skip is not None and (int(row["start"]), int(row["end"])) == skip:
            continue
        studies.append({"title": row.get("title"), "date": row.get("date")})
    return studies


def _deposition_pages_block(page_offset) -> str:
    """The system-message block telling a deposition what its ``Page N:`` markers actually mean.

    The category prompt cannot know: the same markers are the transcript's OWN printed page numbers
    when ``deposition_pages.transcript_page_offset`` established an offset, and mere positions in our
    scanned file when it could not. Citing the second as though it were the first sends a reviewer to
    the wrong page - so when the offset is unknown the model is told to cite nothing.

    Appended to the SYSTEM message, like the other per-row blocks, so the user payload ordering
    (images -> OCR text -> instruction) is untouched.
    """
    if page_offset is None:
        return (
            "\n\nPAGE NUMBERS: the 'Page N:' markers below are positions in our scanned file, NOT "
            "this transcript's own printed page numbers. Do NOT write any page number in the summary "
            "- not the marker numbers and not a number you infer. Still group the pages as instructed "
            "and begin each paragraph with the substance instead of a page reference.\n"
        )
    return (
        "\n\nPAGE NUMBERS: the 'Page N:' markers below ARE this transcript's own printed page "
        "numbers. Cite them exactly as given - they are what a reader uses to find the testimony.\n"
    )


def _standalone_studies_block(studies) -> str:
    """The system-message block naming the diagnostic studies that appear as their OWN sub-document
    in this record, so an embedded records review does not restate what is summarized elsewhere.

    Rendered from the record's already-loaded rows, so this costs no extra AI call. The MODEL does
    the matching: a study's title here and its wording inside the review differ by OCR variance and
    phrasing, which is the problem the duplicate check needed a model call to solve - code-side title
    matching would either miss variants or over-suppress. Returns "" when nothing is listable, so the
    caller sends the system message unchanged.
    """
    lines = []
    for study in studies:
        title = str(study.get("title") or "").strip()
        if not title or title == "-":
            continue
        date = str(study.get("date") or "").strip()
        lines.append(f"- {title} ({date})" if date and date != "-" else f"- {title}")
    if not lines:
        return ""
    return (
        "\n\nDIAGNOSTIC STUDIES THAT APPEAR AS THEIR OWN DOCUMENT ELSEWHERE IN THIS RECORD:\n"
        + "\n".join(lines)
        + "\n\nEach study listed above is summarized in its own right elsewhere in this record. If "
        "the records review inside THIS document reports one of them, do NOT restate it here - "
        "report only the studies the review reports that are not in the list above. The list is "
        "context, not content: never copy it into the summary and never mention that you were "
        "given it."
    )


# Sent AFTER both the images and the OCR text (register G-03). It used to sit between them, which put
# the instruction in the middle of the payload; Google's guidance is context first and the instruction
# last, and a rule buried mid-payload is a plausible cause of the generation misses measured on
# 2026-07-30 (range of motion and capitalisation skipped despite being in the prompt). Worded in the
# past tense about both inputs, since both are now above it.
_MULTIMODAL_INSTRUCTION = (
    "\n\nThe images above are the scanned page(s) of this sub-document, and the OCR text above is "
    "those same pages. Use BOTH - treat the images as authoritative wherever the OCR is garbled, "
    "missing, or from a table, checkbox, or handwriting. Now summarize per the system instructions, "
    "following every rule they state."
)
_OCR_TEXT_HEADER = "OCR TEXT:\n"


# _hit_token_cap moved to services/llm/gemini.py: truncation is read from a vendor's finish reason,
# so it belongs with that vendor's translation, and the audit path needs the same check.


# `model_for(kind, fallback)` lived here and resolved a model PER CALL. It is gone: resolution now
# happens ONCE, at job creation (services/jobs.create_job), and the three models are persisted on the
# Job. That strengthens the property its docstring promised - a resumed job kept only its BODY model
# before, because the title and audit models were re-read from live config on the OpenAI path; now all
# three are pinned, so no config change can split one delivered document across two models.
# summarize_row takes them as arguments instead.


def _generate(model, system_msg, contents, temperature, max_output_tokens=None):
    """One model call -> ``(text, truncated)``.

    ``contents`` is the OCR text, or (multimodal) a list of page-image parts followed by the OCR
    text. ``truncated`` is True when the reply hit the token budget, which callers surface instead
    of storing a half summary as finished.

    Routed through the provider registry rather than google-genai directly, so which vendor answers
    is a config value. A bare string is accepted for convenience and wrapped, because every text-only
    caller passes one.
    """
    settings = get_settings()
    if max_output_tokens is None:
        max_output_tokens = settings.summary_max_output_tokens
    parts = [TextPart(contents)] if isinstance(contents, str) else list(contents)
    response = get_provider().generate_text(
        model=model,
        system=system_msg,
        parts=parts,
        temperature=temperature,
        # Named explicitly even though it is the seam's default, because the default exists to keep
        # thirty-odd monkeypatched test seams working rather than to express intent. On Gemini this
        # selects summary_thinking_budget, which is the value this call already took.
        stage="summarize",
        max_output_tokens=max_output_tokens,
    )
    return response.text, response.truncated


def _backend_name() -> str:
    """Which backend is answering the summarize stage's calls right now.

    Read off the PROVIDER rather than out of config, so a row records what answered rather than what
    the settings happened to say when somebody reads them afterwards. The two can differ: config is
    what the process booted with, and this is the object the call actually went through.
    `get_provider` is cached, so this costs a dict lookup.
    """
    return get_provider().name


# A header line is "AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE." The longest legitimate one across
# 2,912 stored rows is 178 characters DECORATED (~145 raw), so 200 leaves generous headroom while
# staying far under the 512-character column. Not set at 512: that is the storage limit, not a
# plausible length for a header, and a value near it is a symptom rather than a title.
MAX_GENERATED_TITLE = 200


# What is left of summaries.title / summaries.verified_title (both varchar 512) once the decoration
# every stored title carries is subtracted: the ManualCheck tag, the diagnostic tag, and the page
# suffix. Computed from the strings rather than guessed, so it moves if the decoration does.
MAX_STORED_TITLE = (
    512 - len("[ManualCheck] ") - len(" [Diagnostic Study]") - len(" (Pages 9999-9999)")
)


def _usable_title(generated, fallback, source="generated"):
    """The generated header line, or ``fallback`` when the model did not return a header line.

    Measured on the box 2026-08-14: for one row (pages 263-266, 6k of OCR) the title model returned
    ~620 characters of prose. Decorated, that exceeded summaries.title (varchar 512), Postgres
    refused the row, and the per-row commit killed a 124-row job at row 109 - surfacing as the
    generic "something went wrong". Identical output on 2.5-pro and 3.5-flash, so this is the TITLE
    call and not the body model, and it is deterministic at temperature 0: the same document fails
    the same way every time.

    REJECTS rather than truncates, deliberately. A 620-character value is not a long title, it is
    the wrong KIND of answer, and its first 512 characters are not a header either - storing that
    would put a paragraph where a reviewer expects a document header. The row's segmentation title
    is a real header, already shown elsewhere in the UI, so falling back keeps that view coherent.
    Mirrors summary_verify's existing rule that a blank fixed_title falls back to the original.
    """
    cleaned = (generated or "").strip()
    if cleaned and len(cleaned) <= MAX_GENERATED_TITLE:
        return without_address(cleaned)
    logger.warning(
        "%s title unusable (%d chars); falling back to the row title", source, len(cleaned)
    )
    # The FALLBACK needs bounding too, and it did not have it. `row["title"]` is a
    # review_rows.title varchar(512) written verbatim - parse_segment_item only strips, and
    # _store_rows stores `str(row.get("title") or "-")` unmodified - so a 500-character segmentation
    # title plus the decoration above is ~553 characters into a varchar(512). The worker's persist is
    # OUTSIDE the per-row try/except, so that DataError does not fail one row: it kills the whole
    # job, which is the exact incident this function was written to prevent, reached through the one
    # branch the guard skipped.
    #
    # Truncated rather than rejected, unlike the generated case. A 620-character title-model answer
    # is the wrong KIND of answer and its first 512 characters are not a header either; a long
    # SEGMENTATION title is a real header that is merely long, and its front carries the author and
    # facility a reviewer identifies it by. There is also nothing further to fall back to.
    kept = ((fallback or "").strip() or "-")[:MAX_STORED_TITLE]
    if len((fallback or "").strip()) > MAX_STORED_TITLE:
        logger.warning("row title truncated to %d chars to fit the column", MAX_STORED_TITLE)
    return kept


def page_phrase(pages) -> str:
    """``page 7`` / ``pages 7 and 8`` / ``pages 7, 8 and 11`` - the page numbers, in reading order.

    Public because the worker composes the reviewer-facing reason for an unreadable row from it, and
    that sentence must name the same pages, the same way, as the notice the reader sees.

    Deliberately number-agnostic in the sentences that use it: both notices below are worded so the
    singular and plural forms read correctly without a second verb form to keep in step.
    """
    numbers = sorted({int(p) for p in pages})
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    listed = ", ".join(str(n) for n in numbers[:-1])
    return f"pages {listed} and {numbers[-1]}"


# Built in CODE, never model-generated. A model asked to describe a page it cannot read is the exact
# shape that invents content, and this text ships in a medical-legal deliverable. Leads with the word
# a reader is meant to take away ("unintelligible", Adrian's own term for this), then says what
# happened, and carries NO page content - the unreadable text itself never appears anywhere.
#
# Wording follows the house line already in errors.EmptyExtractionError.user_message, minus its
# "may be blank" hedge: that message covers a case where blank and failed cannot be told apart, and
# here they can - these pages ERRORED, so claiming they might be blank would be less accurate.
_NOTICE_LEAD = "Unintelligible: the text recognizer could not read {phrase} of this document"


def unreadable_notice(pages) -> str:
    """The WHOLE body of a row where nothing could be read, in place of a summary."""
    return _NOTICE_LEAD.format(phrase=page_phrase(pages)) + ", so there was no text to summarize."


def notice_pages(unreadable_pages, page_offset) -> list:
    """The page numbers the notice should CITE, in the numbering the summary body above it uses.

    `report["errored"]` is always in RECORD pages - `ocr.extract_pages_with_report` appends the loop
    variable, before any offset. That is right for every category but one. A deposition's body cites
    the TRANSCRIPT's own printed numbers, because `_deposition_pages_block` hands the model markers
    already shifted by `page_offset` and tells it to "cite them exactly as given". So the notice was
    naming pages in a different numbering from the paragraphs directly above it, with nothing saying
    so - on a 60-page transcript the two can be a hundred apart, and both look like page numbers.

    `page_offset` is None for every non-deposition row and for a deposition whose offset could not
    be established; in both cases the body cites record pages or nothing, and the record numbers are
    already the right answer.

    A shifted number that lands at or below zero is NOT emitted. That is a page preceding the
    transcript's own page 1, and inventing "page 0" for it would be a worse citation than the record
    number - so the whole notice falls back to record pages rather than mixing the two. See #259,
    which is the general form of that defect; this only refuses to add to it.
    """
    if page_offset is None:
        return list(unreadable_pages)
    shifted = [int(p) + page_offset for p in unreadable_pages]
    if any(page <= 0 for page in shifted):
        return list(unreadable_pages)
    return shifted


def partial_unreadable_notice(pages) -> str:
    """The sentence appended to a summary built from a row's READABLE pages, naming the rest.

    Separate wording from ``unreadable_notice`` because the reader's question differs: there IS a
    summary above this sentence, and what they need to know is that it does not cover everything.
    """
    return (
        _NOTICE_LEAD.format(phrase=page_phrase(pages))
        + ", so that content is not covered by this summary."
    )


# Built in CODE for the same reason as _NOTICE_LEAD: it ships in a medical-legal deliverable, and a
# model asked to describe pages that were deliberately withheld from it has nothing to describe.
#
# WHY THIS EXISTS. An embedded records review is split off as its own row and EXCLUDED, which is what
# the senior reviewer wants - the evaluation is summarized, the review inside it is not, because it
# recites documents that appear in their own right elsewhere in the deliverable. But an excluded row
# produces no Summary at all, so those pages vanished from the report with nothing said. Asked what
# he wanted, his answer was a tag rather than inclusion (2026-08-26):
#
#     "A potentially easier solution would be to just put in a tag in the summary that there is an
#      embedded medical record review."
#
# Says "which is not summarized here" rather than "was excluded": the reader is being told where the
# content is not, and "excluded" invites the question of who excluded it and why.
_EMBEDDED_REVIEW_LEAD = "{phrase} an embedded review of medical records"


def embedded_review_notice(pages) -> str:
    """The sentence appended to the evaluation that an excluded records-review block belongs to.

    Singular and plural are both written out rather than shared behind a number-agnostic phrasing.
    ``page_phrase`` above takes the other approach - wording its callers so one form fits both - but
    that works because those sentences never need the VERB to agree. Here it does, and "Pages 45
    contain" in a medical-legal deliverable reads as carelessness.
    """
    numbers = sorted({int(p) for p in pages})
    if not numbers:
        return ""
    span = (
        f"Pages {numbers[0]}-{numbers[-1]} contain"
        if len(numbers) > 1
        else f"Page {numbers[0]} contains"
    )
    return _EMBEDDED_REVIEW_LEAD.format(phrase=span) + ", which is not summarized here."


def _row_tags(row) -> tuple[str, str]:
    """The two internal review markers a stored title carries: ``[ManualCheck] `` and
    `` [Diagnostic Study]``.

    Shared by the summary path and the notice path so an unreadable row's header is decorated exactly
    like every other row's - the export strips both either way, and the app shows both.
    """
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""
    return manual_tag, diag_tag


# The three internal markers `_row_tags` applies, as patterns compiled once.
#
# Every whitespace run is BOUNDED, and that is load-bearing rather than tidiness. A leading `\s*` in a
# SUBSTITUTION pattern is super-linear: the engine may start a match at each of n positions and scan a
# whitespace run of length n from each. Sonar reports exactly that ("Simplify this regular expression
# to reduce its runtime, as it has super-linear performance due to backtracking"). `_PAGES_SUFFIX`
# already avoided it by having no leading `\s*` - its previous comment said so - but the
# diagnostic-tag pattern carried one at BOTH ends, and lifting that line into this module turned a
# finding accepted on its old line into a new-code issue that failed the quality gate.
#
# A bounded run is linear by construction: at most 8 repetitions from any starting position. 8 is far
# past anything real, since `_row_tags` emits exactly one space on either side, so nothing these have
# ever matched stops matching. Possessive quantifiers are NOT what makes this safe and have been
# dropped: Sonar's Python analyzer does not appear to model them - they only reached `re` in 3.11 - so
# a pattern whose safety argument rests on `++` reads to it as nested repetition.
#
# The en dash is an escape rather than the character itself: the web view renders ranges with one, so
# it has to be matched, but as a literal it is an ambiguous-Unicode finding (ruff RUF001).
_PAGES_SUFFIX = re.compile(
    r"\(pages\s{1,8}\d{1,9}\s{0,8}[-\u2013]\s{0,8}\d{1,9}\)\s{0,8}$", re.IGNORECASE
)
_MANUAL_CHECK_PREFIX = re.compile(r"^\[ManualCheck\]\s{0,8}")
_DIAGNOSTIC_TAG = re.compile(r"\s{0,8}\[Diagnostic Study\]\s{0,8}")


# A title names the FACILITY, never where it is. Adam Flake flagged it on three records on
# 2026-09-24 ("addresses are being summarized into the document titles") and the reviewers' own
# deliverables agree: 0 of 80 entry headers across their three reference MRRs carry a street, a
# suite, a state or a ZIP. The prompt says so too; this is the deterministic half, because the
# model copies the letterhead's address line with the name often enough to have been reported.
#
# Parsed rather than matched as one pattern. The title is split on its own separators (period,
# comma, spaced dash) and a PIECE is dropped only when the piece as a whole is unmistakably an
# address. A bare city is NOT dropped - "BAKERSFIELD" is indistinguishable from a facility word -
# unless it sits directly before a state + ZIP, where it can only be the address. Per-piece
# fullmatch on short strings keeps every pattern linear, which is the property #162 and #312 had to
# restore after Sonar refused a backtracking one.
_TITLE_SEPARATOR_RE = re.compile(r"(\s{0,4}[.,]\s{0,4}|\s{1,4}[-\u2013]\s{1,4})")
_STREET_SUFFIX = (
    "STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|DRIVE|DR|ROAD|RD|WAY|LANE|LN|HIGHWAY|HWY|PARKWAY|PKWY"
    "|COURT|PLACE|CIRCLE|CIR|TERRACE|TER|PLAZA"
)
_ADDRESS_PIECES = (
    # 5300 CALIFORNIA AVE / 16530 VENTURA BLVD STE 510 / 9330 STOCKDALE HWY SUITE 100
    re.compile(
        rf"\d{{1,6}}[A-Z]?(?: [A-Z0-9'#/&-]{{1,20}}){{1,5}} (?:{_STREET_SUFFIX})"
        r"(?: (?:SUITE|STE|UNIT|#)(?: ?[A-Z0-9-]{1,6})?)?",
        re.I,
    ),
    re.compile(r"(?:SUITE|STE|UNIT|#) ?[A-Z0-9-]{1,6}", re.I),
    re.compile(r"(?:[A-Z][A-Z'-]{1,20} ){0,3}(?:CA|CALIFORNIA|[A-Z]{2}) \d{5}(?:-\d{4})?", re.I),
    re.compile(r"\d{5}(?:-\d{4})?"),
    re.compile(r"(?:PH|PHONE|TEL|FAX|F|T)?:? ?\(?\d{3}\)?[ -]?\d{3}-\d{4}", re.I),
)
_STATE_ZIP = _ADDRESS_PIECES[2]
_BARE_NUMBER = re.compile(r"\d{1,6}")
_CITY = re.compile(r"(?:[A-Z][A-Z'-]{1,20} ){0,2}[A-Z][A-Z'-]{1,20}", re.I)


def without_address(title: str) -> str:
    """``title`` with any street, suite, city-before-ZIP, state/ZIP or phone piece removed.

    A piece is removed together with the separator in front of it, so the header keeps the shape
    ``AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE`` and no doubled punctuation is left behind.
    Everything that is not an address is returned byte for byte."""
    parts = _TITLE_SEPARATOR_RE.split(title or "")
    # parts alternates piece, separator, piece, ... ; pair each piece with the separator before it.
    pieces = [(parts[i - 1] if i else "", parts[i]) for i in range(0, len(parts), 2)]
    drop = [any(p.fullmatch(piece.strip()) for p in _ADDRESS_PIECES) for _, piece in pieces]
    for i in range(len(pieces) - 1):
        nxt = pieces[i + 1][1].strip()
        if not drop[i] and _CITY.fullmatch(pieces[i][1].strip()) and _STATE_ZIP.fullmatch(nxt):
            # Only a city can stand directly before a state and ZIP.
            drop[i] = drop[i + 1] = True
    for i in range(1, len(pieces)):
        if drop[i - 1] and _BARE_NUMBER.fullmatch(pieces[i][1].strip()):
            # `STE. 100` splits on its own period and leaves the number behind.
            drop[i] = True
    if not any(drop):
        return title
    kept = [(sep, piece) for (sep, piece), gone in zip(pieces, drop, strict=True) if not gone]
    if kept and pieces and drop[0]:
        kept[0] = ("", kept[0][1])
    return "".join(sep + piece for sep, piece in kept).strip()


# ONE spelling per provider across a record. Titles are generated one sub-document at a time, each
# from its own pages, so a provider printed "JEFFERY FREESEMANN" on one form, "JEFFERY M. FREESEMANN"
# on the next and "JEFFREY MARK FREESEMANN" on a third got all three in one delivered report. Adam
# Flake, 2026-09-24: "inconsistencies with the provider name with some titles including a middle name
# while others are not". Reviewer corrections on the same record show the same thing done by hand.
#
# The author is the element before the first comma - the TITLE_PROMPT form "AUTHOR, CREDENTIALS. ...".
# Two authors are one provider when they share a surname and a credential AND their first names are
# compatible - the same, one an initial or prefix of the other, or a near spelling (JEFFERY /
# JEFFREY). Measured over the last 30 days on the live box before settling on that: of 47 groups a
# surname + first initial + credential key would merge, 34 differed only in the middle name, 12 in the
# first name's spelling or an initial, and ONE joined two plainly different first names - possibly
# two people, which a record must never collapse. A group is rewritten to its most frequent spelling,
# ties going to the fuller one (more words, then more letters: a middle initial or a first name
# written out carries more of the name), then alphabetically so the choice is deterministic. Only the
# name is touched. A title without a recognisable author element is returned as it came.
_AUTHOR_NAME = re.compile(r"[A-Z][A-Z'\-]{0,24}\.?(?: [A-Z][A-Z'\-]{0,24}\.?){1,4}")
_AUTHOR_CRED = re.compile(r"[A-Z][A-Z.\-/]{0,11}")
_FIRST_NAME_SIMILARITY = 0.75


def _author_parts(title: str):
    """``(name, key)`` for a title that opens with ``NAME, CREDENTIAL``, else ``None``."""
    name, sep, rest = (title or "").partition(", ")
    cred = _AUTHOR_CRED.match(rest) if sep else None
    if not cred or not _AUTHOR_NAME.fullmatch(name):
        return None
    words = name.replace(".", "").split()
    return name, (words[-1], cred.group(0).replace(".", "").upper())


def _first_names_compatible(a: str, b: str) -> bool:
    """Whether two first names can be one person's - see the note above."""
    a, b = a.replace(".", ""), b.replace(".", "")
    short, long_ = sorted((a, b), key=len)
    return (
        a == b
        or long_.startswith(short)
        or difflib.SequenceMatcher(None, a, b).ratio() >= _FIRST_NAME_SIMILARITY
    )


def _spelling_rank(spellings: dict[str, int]):
    return lambda s: (-spellings[s], -len(s.split()), -len(s), s)


def _cluster_spellings(spellings: dict[str, int]) -> dict[str, str]:
    """Each spelling of one surname + credential mapped to its cluster's canonical spelling.

    Strongest spelling first, so each cluster is named after its most frequent member and a weaker
    spelling joins the first cluster whose first name it is compatible with."""
    clusters: list[list[str]] = []
    for name in sorted(spellings, key=_spelling_rank(spellings)):
        first = name.split()[0]
        home = next((c for c in clusters if _first_names_compatible(first, c[0].split()[0])), None)
        if home is None:
            clusters.append([name])
        else:
            home.append(name)
    return {name: cluster[0] for cluster in clusters for name in cluster}


def consistent_authors(titles: list[str]) -> list[str]:
    """``titles`` with every spelling of one provider made the same - see the note above."""
    parts = [_author_parts(t) for t in titles]
    counts: dict[tuple, dict[str, int]] = {}
    for p in (p for p in parts if p):
        spellings = counts.setdefault(p[1], {})
        spellings[p[0]] = spellings.get(p[0], 0) + 1
    chosen = {key: _cluster_spellings(spellings) for key, spellings in counts.items()}
    return [
        title if not p else chosen[p[1]][p[0]] + title[len(p[0]) :]
        for title, p in zip(titles, parts, strict=True)
    ]


# One entry per visit when a work status slip rides with its PR-2. The reviewers, 2026-08-21:
# "mostly when one visit produces a work status report, a PR-2 and office notes on the same day -
# just one entry, usually PR-2 giving the header and no point to mention other things", and in the
# next round "only if they are the same category would we combine (In the case of Work Status, we
# will count that as Category 1)" and "the same doctor". Adam Flake read the two entries as a
# duplicate on 2026-09-24. Folded at EXPORT, over the delivered entries - the rows are untouched,
# so the review page still shows both documents and a reviewer can still split them apart.
#
# A slip folds only into a PR-2 on the SAME date, by the SAME author (after `consistent_authors`),
# both category 1. Measured on the live box over the last 30 days: 20 such pairs across 6 records,
# and every one of the 20 PR-2s already carried its own Work Status point - so the slip is dropped.
# Should a PR-2 ever lack one, the slip's text is appended to it instead of lost.
_WORK_STATUS_TITLE = re.compile(r"\bWORK (?:ACTIVITY )?STATUS\b|\bRETURN[- ]TO[- ]WORK\b", re.I)
_PR2_TITLE = re.compile(r"\bPR-?2\b|\bPROGRESS REPORT\b", re.I)
_WORK_STATUS_POINT = "**Work Status**"


def _visit_kind(entry: dict, category: str, title_key: str) -> str:
    """``"slip"`` for a category 1 work status, ``"pr2"`` for a category 1 PR-2, else ``""``."""
    title = entry.get(title_key) or ""
    if category != "1":
        return ""
    slip, pr2 = bool(_WORK_STATUS_TITLE.search(title)), bool(_PR2_TITLE.search(title))
    if slip and not pr2:
        return "slip"
    return "pr2" if pr2 and not slip else ""


def _same_visit(a: dict, b: dict, a_author, b_author) -> bool:
    """One author (after `consistent_authors`) on one date."""
    return bool(a_author and b_author and a_author[0] == b_author[0]) and (
        a.get("summaryDate") == b.get("summaryDate")
    )


def fold_work_status(entries: list[dict], categories: list[str], title_key: str) -> list[dict]:
    """``entries`` with each same-visit work status slip folded into its PR-2 - see above."""
    authors = [_author_parts(e.get(title_key) or "") for e in entries]
    kinds = [_visit_kind(e, c, title_key) for e, c in zip(entries, categories, strict=True)]
    pr2s = [j for j, k in enumerate(kinds) if k == "pr2"]
    folded: dict[int, int] = {}
    for i in (i for i, k in enumerate(kinds) if k == "slip"):
        home = next(
            (j for j in pr2s if _same_visit(entries[i], entries[j], authors[i], authors[j])), None
        )
        if home is not None:
            folded[i] = home
    out = []
    for j, entry in enumerate(entries):
        if j in folded:
            continue
        text = entry.get("summaryText") or ""
        for i in (i for i, h in folded.items() if h == j and _WORK_STATUS_POINT not in text):
            text = f"{text} {entries[i].get('summaryText') or ''}".strip()
        out.append({**entry, "summaryText": text})
    return out


def presentable_title(title: str) -> str:
    """``title`` with every internal review marker removed, ready for a delivered document.

    Lives next to `_row_tags`, which APPLIES those markers, because the two have to agree and they
    did not: `_row_tags`' own docstring says "the export strips both either way", and that was true
    of the review export and false of the bundle export. `services/bundles.py` built its entries
    straight from `summarize_row`'s decorated `summaryTitle`, so a bundle-summarize download shipped
    `[ManualCheck] `, ` [Diagnostic Study]` and ` (Pages X-Y)` into the Word document a client reads.
    `[ManualCheck]` carries the furthest: the flag it keys on is set on most rows.

    None of the three appears in any of the eight human-written deliverables this output is measured
    against - and note the tag is BRACKETED for a reason, since the bare words "Diagnostic Study"
    do occur in their prose.

    The page suffix is stripped rather than kept because a reviewer editing a row's boundary leaves
    the stored one stale. A caller that wants it re-applies it from the row's CURRENT range.
    """
    # A local rather than reassigning the parameter: the version this replaced worked on a local
    # derived from `summary.effective_title()`, so lifting it into a function turned those writes into
    # parameter reassignment - a code smell in its own right, and one that reads as if the caller's
    # value were being mutated.
    presentable = _MANUAL_CHECK_PREFIX.sub("", (title or "").strip())
    presentable = _PAGES_SUFFIX.sub("", presentable).rstrip()
    return without_address(_DIAGNOSTIC_TAG.sub(" ", presentable).strip())


def _unreadable_output(row, unreadable_pages) -> dict:
    """The output_dict for a row whose pages could not be READ at all.

    Returned instead of raising ``EmptyExtractionError`` so the row is DELIVERED carrying a notice
    rather than vanishing from the report with nothing said. Its caller writes a real Summary from
    this, which is what puts the notice in the row's own entry in both export paths.

    Every model-written field is None/False, and that is the point: no model saw this row, so
    ``model=None`` beside ``unreadablePages`` is what tells a notice row apart from one that WAS
    summarized off its readable pages. No `sourceText` either - there is none, and storing "" would
    record an empty extraction as a successful one.

    The header keeps the ROW's own title and date. Segmentation read those from a whole window of
    pages, so they commonly survive a page the recognizer could not read; where they did not, the
    row's "-" sentinel carries through and the entry is identified by the page range its title
    already carries, rather than by a blank field.

    No DOI prefix, unlike every summarized row: the prefix qualifies summary content, and there is
    none here. `_export_title_and_text` re-adds a DOI only when the stored body already carries one,
    so this stays out of the deliverable rather than appearing as a bare "**DOI**:".
    """
    manual_tag, diag_tag = _row_tags(row)
    pages = sorted({int(p) for p in unreadable_pages})
    page_label = f"Pages {row['start']}-{row['end']}"
    title = str(row.get("title") or "").strip()
    if not title or title == "-":
        # Degrade to the page range rather than to the bare "-" sentinel. It has to go in the title
        # PROPER, not the usual "(Pages X-Y)" suffix, because `_export_title_and_text` strips that
        # suffix from every entry - so a notice row with no header would otherwise reach the
        # deliverable identified by nothing at all.
        decorated = f"{manual_tag}{page_label}{diag_tag}"
    else:
        decorated = f"{manual_tag}{title}{diag_tag} ({page_label})"
    return {
        "summaryDate": row["date"],
        "summaryTitle": decorated,
        "manualCheck": manual_tag,
        "truncated": False,
        "summaryText": unreadable_notice(pages),
        "verified": False,
        "verifiedText": None,
        "verifiedTitle": None,
        "verifyIssues": None,
        "sourceText": None,
        "model": None,
        "bodyFallbackFrom": None,
        "titleModel": None,
        "auditModel": None,
        # Nothing ran, so no backend answered. NULL here means the same as NULL in the model fields
        # beside it - not "Gemini by default".
        "backend": None,
        "promptFingerprint": None,
        "auditFingerprint": None,
        "unreadablePages": pages,
        # Always empty on a notice-only row. Nothing was summarized, so there is no body for an
        # embedded-review tag to qualify - appending "not summarized here" beneath "there was no text
        # to summarize" would say the same thing twice about different pages.
        "embeddedReviewPages": [],
        # The body IS the notice. Distinct from a partial row (which also carries pages here) because
        # the caller treats only this case as a row that could not be summarized.
        "noticeOnly": True,
    }


def _resolved_models(model, title_model, audit_model) -> tuple[str, str, str]:
    """Fill in whichever of the three summarize models the caller omitted, from config.

    The worker passes the values persisted on the Job, so a resumed job cannot switch models
    mid-document; only a standalone caller reaches these defaults.
    """
    settings = get_settings()
    return (
        model or settings.model_for("body"),
        title_model or settings.model_for("title"),
        audit_model or settings.model_for("audit"),
    )


def _build_system_message(pdf_path, row, prompt, standalone_studies):
    """The system message for one row, its prompt fingerprint, and the deposition facts behind it.

    Returns ``(system_msg, prompt_fingerprint, deposition, page_offset)``. The last two are returned
    rather than kept private because the caller needs both afterwards: ``deposition`` decides whether
    stored OCR may be reused, and ``page_offset`` labels both the re-extraction and the trailing
    notice. Discovering that offset READS THE PDF, so it is named in the return rather than left as a
    side effect of something called "build system message".
    """
    # Prepend the shared rules that can bind on THIS category (applies to DB-resolved and fallback
    # prompts alike, and to any future category - build_preamble defaults an unknown id to everything).
    preamble = build_preamble(row["category"])
    system_msg = preamble + prompt
    # Fingerprint the PROMPT TEXT only, and do it HERE - before the per-row blocks below are appended.
    # Those carry row and document DATA, so hashing them would give two rows on an identical prompt
    # different fingerprints and make the cohort query this exists to enable useless.
    prompt_fingerprint = summary_prompt_fingerprint(preamble, prompt)
    # E-08: append the record's other diagnostic studies AFTER the category rules, so the list reads
    # as a qualification of the rule that just told the model to take studies out of the embedded
    # review. Appended to the SYSTEM message, not the user content, so the payload ordering
    # (images -> OCR text -> instruction) is untouched.
    if standalone_studies and str(row["category"]) in _EMBEDDED_REVIEW_CATEGORIES:
        system_msg += _standalone_studies_block(standalone_studies)
    # Tell a treating report which encounter it is, so a recap of the previous visit can be told
    # apart from this visit's own findings by date rather than by guesswork.
    if str(row["category"]) in _CURRENT_VISIT_CATEGORIES:
        system_msg += _document_date_block(row.get("date"))

    # Depositions are summarized in groups of consecutive transcript pages, so this category needs to
    # SEE where each page ends. The stored text cannot be reused for them: page boundaries cannot be
    # retrofitted onto text that was already concatenated without them, so a marked re-extraction is
    # the only way. Confined to category 9 - markers in every category's input would push page numbers
    # into ordinary summaries and pollute the duplicate check's similarity scoring.
    deposition = str(row["category"]) == "9"
    page_offset = None
    if deposition:
        # Label the markers with the TRANSCRIPT's own printed page numbers, discovered once. When the
        # offset cannot be established the markers fall back to record pages and the prompt is told
        # not to cite them at all: a citation that looks like a transcript page but is not one sends a
        # reviewer to the wrong page, which is worse than giving them no page at all.
        page_offset = transcript_page_offset(pdf_path, row["start"], row["end"])
        system_msg += _deposition_pages_block(page_offset)
    return system_msg, prompt_fingerprint, deposition, page_offset


def _row_source_text(pdf_path, row, deposition, page_offset) -> tuple[str, list]:
    """The row's OCR text, and which of its pages the recognizer FAILED on.

    Reuse the duplicate check's OCR when it exists: it ran the SAME extraction over the SAME pages
    and persisted it per row, so a second full pass is pure waste - on a 1500-page record that is
    ~45 minutes of OCR done twice. Blank text is not reused, so a page whose OCR failed the first
    time is retried here rather than being permanently condemned to EmptyExtractionError.

    STOPS at the extraction. What an empty result MEANS is the caller's decision, and both answers
    leave summarize_row entirely - a notice-only dict, or EmptyExtractionError that two callers catch
    by type - so neither can live behind a helper call.
    """
    text = "" if deposition else (row.get("source_text") or "").strip()
    # Which of this row's pages the recognizer FAILED on, as opposed to read cleanly and found empty.
    # Seeded from the row when the caller knows (it can read `page_texts.extract_ok`, which this
    # DB-free module cannot), then OVERRIDDEN by a fresh extraction below - what just happened is
    # authoritative over what a previous stage recorded, because an errored page is often a transient
    # timeout that a later attempt reads fine, and announcing a page as unintelligible when this run
    # read it is worse than saying nothing.
    unreadable_pages = sorted({int(p) for p in (row.get("unreadable_pages") or [])})
    if not text:
        pages = list(range(int(row["start"]), int(row["end"]) + 1))
        # The REPORTING extractor, so a row that produced no text can say WHY. The plain variant
        # collapses a failed page and a legitimately blank one into the same silent skip, and that is
        # exactly the distinction the notice below turns on. It also retries an errored page once on
        # the way through, so a transient Tesseract timeout gets another chance before it is
        # announced to a client.
        text, report = extract_pages_with_report(
            pdf_path, pages, mark_pages=deposition, page_label_offset=page_offset or 0
        )
        unreadable_pages = sorted(report["errored"])
    return text, unreadable_pages


def _doi_lead(injury_date) -> str:
    """The house DOI prefix with EXACTLY one trailing space, or "" when the document states none.

    House grammar (see summary_doi): "**DOI**: <value>." - colon-space, period terminator. Stored
    summaries written before 2026-07-29 carry the old "**DOI**:<value>," form and stay readable;
    summary_doi.doi_prefix parses both.

    THE TRAILING SPACE BELONGS TO THE PREFIX, not to the interpolation. Both bodies used to be built
    as f"{doi_final} {body}", so a row whose document states no injury date stored a body beginning
    with a space. Nothing downstream strips it: effective_text() returns it verbatim,
    _export_title_and_text only prepends, and the Word renderer writes the title, then ". ", then the
    body unmodified. Those entries shipped with TWO spaces after the title while their DOI-carrying
    neighbours shipped with one - and the linked PDF showed one either way, because HTML collapses
    whitespace. That is #115 (a double space in the letter) and #158 (the two renderers disagreeing)
    arriving together, one row at a time.

    `scripts/backfill_doi.py` bakes it in permanently: apply_doi_prefix(" Body.", "09/25/23")
    returns "**DOI**: 09/25/23.  Body."
    """
    if injury_date in ("", "-"):
        return ""
    return f"**DOI**: {injury_date}. "


def _trailing_notices(verified_text, unreadable_pages, embedded_review_pages, page_offset):
    """The sentences appended AFTER the audit, and the verified body carrying them.

    Returns ``(partial_notice, verified_text)``. ``verified_text`` MUST be returned and rebound by the
    caller: it is a str, so appending to it in here could never reach them.

    Appended after the verify pass deliberately. The audit checks the body against the SOURCE TEXT,
    and these sentences are by definition not in that source - letting the audit see them invites it
    to "correct" an unsupported claim, or to count one as a faithfulness issue and flag the row. They
    are applied to the verified body too, so the notice survives whichever body effective_text()
    delivers, and after sentence_case_caps_runs so that transform never rewrites them.

    THE ORDER IS A DECISION. A row can be both partly unreadable and followed by an excluded review;
    when it is, the reader is told what this summary does not cover before being told what sits next
    to it, which is decreasing relevance to the body above. The two appends are collapsed into one,
    which is exact - appending an empty string is a no-op - but it is the same order.
    """
    partial_notice = ""
    if unreadable_pages:
        # Cited in the SAME numbering as the body above it - see `notice_pages`. For a deposition the
        # body cites transcript pages, so a record-page notice put two different numbering systems in
        # one summary with nothing marking the change.
        partial_notice = " " + partial_unreadable_notice(
            notice_pages(unreadable_pages, page_offset)
        )
    if embedded_review_pages:
        partial_notice += " " + embedded_review_notice(embedded_review_pages)
    if verified_text is not None:
        verified_text += partial_notice
    return partial_notice, verified_text


_PAGE_MARKER = re.compile(r"^Page (\d+):", re.MULTILINE)
_CITED_THROUGH = re.compile(
    r"^\s*On pages?\s+\d+\s*(?:to|and|-)\s*(\d+)", re.IGNORECASE | re.MULTILINE
)


def _log_incomplete_deposition(summary, text, row) -> None:
    """WARNING when a deposition body's citations stop well short of the pages it was handed.

    LOGS ONLY. It changes no output, and that restraint is deliberate: a body can stop early for
    two different reasons and only one of them is visible from here. Hitting the token cap is
    reported by the provider as `truncated`, and `_retry_if_truncated` acts on it. A model that
    simply emits an end-of-reply after four paragraphs of seventy reports `finish_reason=stop` -
    it looks, to every layer above, exactly like a finished summary - and nothing anywhere records
    that the transcript was abandoned.

    The two are indistinguishable on the delivered row, which is why a reviewer reporting "cut off
    partway through" could not be attributed to either. This is the instrument that separates them:
    a row carrying this line and NOT the truncation warning above is the second case.

    The comparison needs no inference. `extract_pages_with_report(mark_pages=True)` prefixes every
    page with ``Page <n>:`` using the SAME offset the prompt tells the model to cite back, so the
    last marker in the source and the last ``On pages N to M`` in the body are in one numbering by
    construction. Half is a deliberately loose floor - a deposition legitimately spends its closing
    pages on exhibits and signatures, so the intent is to catch the six-fold shortfall, not to
    police the last page.
    """
    markers = _PAGE_MARKER.findall(text or "")
    cited = _CITED_THROUGH.findall(summary or "")
    if not markers or not cited:
        return
    last_given, last_cited = int(markers[-1]), max(int(c) for c in cited)
    if last_cited >= last_given / 2:
        return
    logger.warning(
        "deposition body on pages %s-%s cites through page %d of %d and stops "
        "(%d group(s) for %d page(s)); the summary covers part of the transcript",
        row["start"],
        row["end"],
        last_cited,
        last_given,
        len(cited),
        len(markers),
    )


def _retry_if_truncated(model, system_msg, body_contents, row, summary, truncated):
    """Re-ask ONCE at a wider cap when the body was cut off. Returns ``(summary, truncated)``.

    A reply that hits the token cap is a HALF SUMMARY that reads like a whole one - it stops
    mid-clause and nothing downstream says so. `summaries` has no column for it, and the only
    surface is `manual_check`, which `tasks.py` also sets from the row's own flag and from a
    fallback-model answer and which carries on 79% of rows, so it cannot single this out. The cap's
    own comment records the same failure at 2048 ("cut long category-1 narratives off mid-sentence
    and the partial reply was stored as if it were finished"); raising it to 8192 moved the
    threshold rather than removing it, and category 9 sits the wrong side of the new one.

    THE RETRY IS THE WHOLE FIX, not a bigger cap - see `summary_truncation_retry_multiplier` for
    why, and for the measurement that says a truncated AUDIT wants the opposite treatment.

    Preference order when both replies exist: one that FINISHED beats one that did not, and between
    two truncated replies the longer covers more of the transcript. The first attempt is truncated
    by construction here, so a complete retry always wins.

    A raising retry is swallowed deliberately. We already hold a usable, if partial, summary; losing
    it to an exception raised while trying to improve it would turn a degraded row into a failed
    job, which is strictly worse for the reviewer.
    """
    settings = get_settings()
    multiplier = settings.summary_truncation_retry_multiplier
    if not truncated or multiplier <= 1:
        return summary, truncated
    wider = int(settings.summary_max_output_tokens * multiplier)
    logger.warning(
        "summary body hit the %d-token cap on pages %s-%s (%d chars); re-asking at %d",
        settings.summary_max_output_tokens,
        row["start"],
        row["end"],
        len(summary or ""),
        wider,
    )
    try:
        retried, still_truncated = _generate(
            model,
            system_msg,
            body_contents,
            temperature=settings.summary_temperature,
            max_output_tokens=wider,
        )
    except Exception:  # noqa: BLE001 - a partial body beats losing the row; see the docstring
        logger.warning(
            "the wider re-ask failed on pages %s-%s; keeping the truncated body",
            row["start"],
            row["end"],
        )
        return summary, truncated
    # AN EMPTY REPLY IS NEVER AN IMPROVEMENT, and this is checked BEFORE the preference below.
    # Without it `not still_truncated` accepts a reply that is empty and merely reports itself
    # finished, so the usable partial body is discarded and nothing ships - a worse outcome than
    # the truncation this function exists to fix.
    #
    # Reachable rather than theoretical: both providers normalise a missing reply to "" and never
    # None (`gemini.py` and `openai.py` both `(response.text or "").strip()`), and neither sets
    # `truncated` unless the finish reason says so. A safety block, or a model that simply stops,
    # lands exactly here. The asymmetry is the tell that it was an oversight: empty-AND-truncated
    # was already safe, because `len("") > len(partial)` is False.
    if not (retried or "").strip():
        logger.warning(
            "the wider re-ask came back empty on pages %s-%s; keeping the truncated body",
            row["start"],
            row["end"],
        )
        return summary, truncated
    if not still_truncated or len(retried) > len(summary or ""):
        logger.warning(
            "the wider re-ask returned %d chars on pages %s-%s (truncated=%s); keeping it",
            len(retried or ""),
            row["start"],
            row["end"],
            still_truncated,
        )
        return retried, still_truncated
    return summary, truncated


def _generate_body(model, system_msg, body_contents, row):
    """The summary body, with the 429 fallback. Returns (summary, truncated, model, fallback_from).

    The body model can be unavailable rather than slow: on 2026-08-13 Vertex refused 2.5-pro for this
    project outright and every summarize job failed. `generate_with_retry` already rides out transient
    429s; this catches the case where its whole budget is spent and the 429 is still coming, and
    answers the row with a lesser model instead of losing it.

    Three properties this deliberately has:

      FALLBACK, NOT RACE. Only after retries are exhausted. Firing both and taking the first would
      double the load on a pool that is already refusing us.

      LOUD. Logged at WARNING with both models named. A silent downgrade would reproduce the exact
      problem this pipeline keeps hitting - output nobody can attribute to a model.

      TWO WAYS IN, not one. The obvious path is Dynamic Shared Quota: transient 429s that
      generate_with_retry rides out until its budget is spent. The second is a spent per-day /
      free-tier allowance, which that seam re-raises IMMEDIATELY without retrying - and it still
      carries `code == 429`, so it lands here too. That is the behaviour we want (a different model
      has a different allowance) but it is worth naming, because reading this without it suggests DSQ
      is the only path in. `errors.is_daily_quota` distinguishes the two if that ever matters.

      RECORDED PER ROW. `model` is reassigned and RETURNED, so the caller's provenance - and
      therefore `summaries.model` - names the model that ACTUALLY answered, not the one the job
      intended. Job-level provenance cannot express this: models.py resolves the three models once at
      job creation, on purpose, so a resumed job cannot switch mid-flight. The row is the only place
      this fact fits, and `_build_summary` already writes it there. A helper that failed to hand the
      rebound name back would make a fallen-back row claim the model that refused it.

    `get_settings()`, never a fresh `Settings()`: the fallback tests drive this through setenv plus
    `get_settings.cache_clear()`, and a fresh construct would not obviously fail.
    """
    settings = get_settings()
    fallback_model = settings.summary_body_fallback_model
    try:
        summary, truncated = _generate(
            model,
            system_msg,
            body_contents,
            temperature=settings.summary_temperature,
            max_output_tokens=settings.summary_max_output_tokens,
        )
    except Exception as exc:
        if not (fallback_model and fallback_model != model and is_rate_limited(exc)):
            raise
        logger.warning(
            "body model %s exhausted its retries on 429 for pages %s-%s; falling back to %s",
            model,
            row["start"],
            row["end"],
            fallback_model,
        )
        summary, truncated = _generate(
            fallback_model,
            system_msg,
            body_contents,
            temperature=settings.summary_temperature,
            max_output_tokens=settings.summary_max_output_tokens,
        )
        body_fallback_from, model = model, fallback_model
    else:
        body_fallback_from = None
    # AFTER the fallback, and against whichever model answered: a row that fell back to the lesser
    # model can be truncated too, and re-asking the model that just refused us would be the race
    # the fallback exists to avoid.
    summary, truncated = _retry_if_truncated(
        model, system_msg, body_contents, row, summary, truncated
    )
    return summary, truncated, model, body_fallback_from


def _house_body(text: str, deposition: bool) -> str:
    """The deterministic house-style passes over a body, generated or audited alike: capitals, then
    one paragraph - see `house_style.one_paragraph` - for everything but a deposition, which is
    grouped by page on purpose."""
    text = sentence_case_caps_runs(text)
    return text if deposition else one_paragraph(text)


def _verified_outputs(audit_model, row, text, summary, title, doi_lead):
    """Audit the body and title, and turn the verdict into the four fields the row stores.

    Returns ``(verified_text, verified_title, verify_issues, verify_ran)``.

    `verify_ran` is taken from the audit's own `ok` and set OUTSIDE the issues check. That ordering is
    load-bearing: an audit that RAN and found nothing is verified, with no issues, which is most
    production rows. Returning early on an empty issue list - the obvious shape for this function -
    would flip `verified` to False for exactly those rows and store a false record on a medical
    summary, one no later query could tell apart from "the audit crashed and nobody looked".

    `verify_issues` stays None on a clean audit rather than becoming []. The column is JSON, and NULL
    versus '[]' changes what a later query means.

    `result.get("ok")`, never `result["ok"]`: test_provenance's audit stub returns no `ok` key at all.

    `doi_lead` is PASSED IN rather than re-derived. The caller computes it once and uses it for the
    raw body too, so the two renderings agree by construction. Two independent derivations of the DOI
    prefix disagreeing by one space is precisely what #115 and #158 were.
    """
    verified_text = None
    verified_title = None
    verify_issues = None
    deposition = str(row["category"]) == "9"
    manual_tag, diag_tag = _row_tags(row)

    # The SAME gate generation uses, and it has to be. The document date is the sole switch for
    # audit house rule 6 ("content the SOURCE attributes to an EARLIER date than this document's
    # own date is a recap of a prior encounter and does not belong in this summary. ... This rule
    # applies ONLY when a document date is given below"), and `verify_summary` emits the date
    # block whenever the value is non-empty.
    #
    # Passing it unconditionally armed that rule on every category, including the ones generation
    # deliberately withholds it from. `_CURRENT_VISIT_CATEGORIES` exists because "a medico-legal
    # evaluation (12, 13) is REQUIRED to carry the injury history", and
    # `test_other_categories_are_not_given_a_document_date` states the consequence outright: the
    # rule "would only cost tokens and risk dropping wanted content".
    #
    # So the audit was enforcing on 3/5/9/12/13/100 exactly the rule the generator was forbidden
    # to state, and the rewrite is ACCEPTED - `prior_visit` is not in `_CORRECTION_ONLY_ISSUES`,
    # so `_drops_required_headings` returns False, `verified_text` is stored, and
    # `effective_text()` prefers it over the raw body. A category-13 evaluation's History of
    # Injury, Previous Injury and Treatment points are all attributed to earlier dates by their
    # source. Category 9 is worse still: a deposition's whole substance is testimony about earlier
    # events, and `_drops_deposition_structure` only compares paragraph and citation COUNTS, so a
    # rewrite that keeps every "On pages N to M" opener and empties its substance passes the guard.
    #
    # This is the generation-versus-audit drift the summary_verify docstring already records for
    # house rule 4 (#109, where the audit deleted directions the generator was required to add).
    # Rule 6's generation-side counterpart was the one omission from that module's
    # "must be edited together" list.
    result = verify_summary(
        audit_model,
        text,
        summary,
        title=title,
        document_date=(
            row.get("date") if str(row["category"]) in _CURRENT_VISIT_CATEGORIES else None
        ),
    )
    verify_ran = bool(result.get("ok"))
    if not verify_ran:
        # `ok` False folds three events into one value - nothing to audit, the reply hit the
        # token cap, and something raised. #285 put `truncated` and the token counts on EVERY
        # return path precisely so the caller handling a failure could tell them apart, and this
        # caller read neither, so the distinction stopped at the function boundary.
        #
        # Logged HERE rather than left to `summary_verify`, which names the cap but not the row:
        # its warning cannot say WHICH summary went unaudited, and that is the only question a
        # reviewer's report raises. The sibling warnings below use the same page-range form.
        #
        # WARNING because this body ships with no faithfulness check behind it. Measured on the
        # box 2026-09-10: 36 of 1,155 audited rows (3.1%), every one delivered - so the line is
        # rare enough to read, and it is the ONLY record of the cause. `Summary` stores the
        # outcome (`verified`) and has no column for the reason.
        logger.warning(
            "summary audit did not complete on pages %s-%s "
            "(truncated=%s, output tokens=%s); shipping the body unaudited",
            row["start"],
            row["end"],
            result.get("truncated"),
            result.get("output_tokens"),
        )
    if result["issues"]:
        issue_types = {
            str(issue.get("type") or "") for issue in result["issues"] if isinstance(issue, dict)
        }
        # FIRST, and ahead of the issue-type test the two guards below share. Those stand down
        # for any issue outside `_CORRECTION_ONLY_ISSUES`, on the reasoning that a substantive
        # complaint earns a substantive rewrite - which holds while a rewrite is a correction.
        # It does not hold for a reply that is the title: the audit can report `unsupported_claim`
        # and hand back the header, and every guard after this one would step aside for it.
        if _restates_the_title(result["fixed_text"], title):
            # Same remedy as its siblings: leave `verified_text` None so `effective_text()` falls
            # back to the raw body, and store the issues anyway so the reviewer still sees what
            # was flagged. WARNING because the rate is the measurement - on the self-hosted model
            # this is the failure a reviewer reported, and on gemini it should never fire.
            logger.warning(
                "verify pass returned only the title on pages %s-%s (issues: %s); keeping raw body",
                row["start"],
                row["end"],
                ",".join(sorted(issue_types)),
            )
        elif _drops_required_headings(summary, result["fixed_text"], issue_types):
            # Keep the RAW body by leaving verified_text None: effective_text() then falls back to
            # summaryText. The issues are still stored below, so the reviewer sees what was
            # flagged, and this logs at WARNING so the guard's firing rate stays measurable rather
            # than becoming an invisible silent correction.
            logger.warning(
                "verify pass dropped bold headings on pages %s-%s (issues: %s); keeping raw body",
                row["start"],
                row["end"],
                ",".join(sorted(issue_types)),
            )
        elif deposition and _drops_deposition_structure(summary, result["fixed_text"]):
            # Same remedy for the deposition format: the page grouping and its citations are what a
            # reviewer navigates by, so a rewrite that flattens them is rejected and the raw body
            # ships. Logged at WARNING for the same reason - a silent structural correction is
            # indistinguishable from the model never having produced the structure.
            logger.warning(
                "verify pass flattened the deposition grouping on pages %s-%s; keeping raw body",
                row["start"],
                row["end"],
            )
        else:
            # The audit may reintroduce capitals while fixing something else, so the transform runs
            # over its output too - the verified text is what effective_text() delivers.
            verified_text = f"{doi_lead}{_house_body(result['fixed_text'], deposition)}"
        verify_issues = result["issues"]
        # The title is corrected INDEPENDENTLY of the body, including when the body rewrite was
        # rejected above: effective_title() and effective_text() fall back separately, and a wrong
        # date or laterality in the title is exactly what this pass exists to catch.
        #
        # Decorated exactly like the stored title, so a verified title is a drop-in replacement
        # in every view; the export path strips the tags either way.
        # Bounded by the same guard as the generated title, which it did NOT have. The audit's
        # schema declares a plain {"type": "string"} with no maxLength - and Gemini ignores
        # maxLength anyway, as the note above says - so nothing upstream bounds this either, and
        # it is written to verified_title, the sibling varchar(512) the original guard never
        # covered. An over-long correction is REJECTED here rather than truncated, which falls
        # out of _usable_title returning `title`: an unusable rewrite then equals the current
        # title and no verified_title is stored, exactly as a rejected BODY rewrite keeps the raw
        # body.
        fixed_title = _usable_title(result.get("fixed_title"), title, source="audited")
        if fixed_title and fixed_title != title:
            verified_title = (
                f"{manual_tag}{fixed_title}{diag_tag} (Pages {row['start']}-{row['end']})"
            )
    return verified_text, verified_title, verify_issues, verify_ran


def summarize_row(
    pdf_path,
    row,
    model=None,
    prompt=None,
    verify=None,
    standalone_studies=None,
    title_model=None,
    audit_model=None,
):
    """Summarize one sub-document row -> the legacy output_dict shape.

    row: {start, end, category, date, injury_date, flag} and OPTIONALLY ``source_text`` - the OCR
    text of exactly those pages, which the duplicate check already extracted and stored. When it is
    present and non-blank this reuses it instead of OCRing the same pages a second time; blank or
    absent means OCR here as before, so a page whose OCR failed still gets another chance.
    ``_store_rows`` carries ``source_text`` across edits keyed by (start, end), so reused text
    always belongs to the row's current page range.

    ``prompt`` is the category's summary system prompt (blueprints resolve it DB-first via
    catalog.get_prompt and inject it); when omitted it falls back to the hardcoded prompts.py dict.
    ``verify`` runs the faithfulness verify pass (defaults to settings.summary_verify); callers pass
    False to skip it (e.g. bundle export). The injury date is NOT read here - it comes from
    ``row["injury_date"]``, which segmentation established per sub-document in isolation, so a
    reviewer's correction survives into the delivered summary.

    ``standalone_studies`` is DOCUMENT-set context, not row data: ``[{title, date}]`` for the record's
    other diagnostic studies (build it with standalone_studies_from_rows). It reaches the model only
    for the categories whose rules discuss an embedded records review, so that review does not restate
    a study which is summarized in its own right elsewhere in the record.

    ``model`` / ``title_model`` / ``audit_model`` are the three summarize calls. The worker passes the
    values persisted on the Job, so a resumed job cannot switch models mid-document; a standalone
    caller that omits them falls back to config. They are NOT re-resolved per call - see the note
    where ``model_for`` used to live.
    """
    settings = get_settings()
    model, title_model, audit_model = _resolved_models(model, title_model, audit_model)
    if verify is None:
        verify = settings.summary_verify
    if prompt is None:
        key = f"category_{int(row['category']):02d}" if row["category"] != "100" else "category_100"
        prompt = prompts.get(key, prompts["category_100"])
    system_msg, prompt_fingerprint, deposition, page_offset = _build_system_message(
        pdf_path, row, prompt, standalone_studies
    )
    text, unreadable_pages = _row_source_text(pdf_path, row, deposition, page_offset)
    # Pages of an excluded records-review block that belongs to THIS row. Seeded by the worker, which
    # is the only layer that can see the neighbouring rows - this module is deliberately DB-free, the
    # same reason `unreadable_pages` arrives as row data rather than being looked up here.
    embedded_review_pages = sorted({int(p) for p in (row.get("embedded_review_pages") or [])})
    if not text.strip():
        if unreadable_pages:
            # Deliver the row carrying a notice instead of losing it from the report. ONLY a genuine
            # extraction failure is announced: a row that read cleanly and holds no words - a film, a
            # photograph, a separator sheet - falls through to the raise below and stays silent,
            # because there is nothing about it to explain.
            return _unreadable_output(row, unreadable_pages)
        # Fail fast with a clear reason: sending empty text to Gemini yields a cryptic
        # "Model input cannot be empty" 400. Blank/image-only pages hit this.
        raise EmptyExtractionError(f"no OCR text for pages {row['start']}-{row['end']}")

    # Summary body runs at settings.summary_temperature (default 0.0 for determinism); the title is
    # pure extraction, always 0. When multimodal is on, the body also gets the page images (OCR text
    # alone garbles tables/handwriting); a rasterize failure degrades to OCR-only rather than failing
    # the row. The title stays OCR-text-only (cheaper and adequate).
    body_contents = text
    if settings.summary_multimodal:
        try:
            # Order matters (G-03): page images, then the OCR text, then the instruction LAST. The
            # instruction used to sit between the images and the text, i.e. in the middle of the
            # payload, against Google's context-first / instruction-last guidance.
            # The 15-page cap is passed EXPLICITLY now rather than read inside the rasteriser.
            # Segmentation shares that helper and runs to 100 pages, so a baked-in cap would have
            # silently truncated one of the two callers.
            body_contents = page_image_parts(
                pdf_path, row["start"], row["end"], settings.summary_image_max_pages
            ) + [
                TextPart(_OCR_TEXT_HEADER + text),
                TextPart(_MULTIMODAL_INSTRUCTION),
            ]
        except Exception as exc:  # noqa: BLE001 - degrade to OCR-only; never fail a summary on this
            logger.warning(
                "multimodal rasterize failed for pages %s-%s; using OCR-only: %s",
                row["start"],
                row["end"],
                exc,
            )
    # `model` is REBOUND from the return: it names the model that actually answered, and the output
    # dict records it as this row's provenance.
    summary, truncated, model, body_fallback_from = _generate_body(
        model, system_msg, body_contents, row
    )
    if deposition:
        # Here rather than inside `_generate_body`, which is handed neither the source text nor
        # the row's deposition-ness - and passing both would take it, and the helper below it,
        # past the seven-parameter ceiling this module already sits at.
        _log_incomplete_deposition(summary, text, row)
    title, _ = _generate(title_model, TITLE_PROMPT, text, temperature=0.0)
    # The title call has no response_schema, and Gemini does not enforce maxLength on strings even
    # when one is declared, so NOTHING upstream bounds this. Guard here, before it is decorated and
    # written to a varchar(512).
    title = _usable_title(title, row.get("title"))
    # Deterministic capitalisation fix on the BODY only (the title is an ALL CAPS header by design).
    # The prompt rule and the audit rule both stay: this catches what they miss, which was 22% of
    # measured rows. Applied before the verify pass so the audit reads the text a reader will see.
    summary = _house_body(summary, deposition)

    # The row IS the source of truth for the injury date. It was read once, per sub-document and in
    # isolation, at the END of segmentation (see segment_engine.run_segmentation), so a reviewer who
    # corrects it on the review page has that correction reach the delivered summary.
    #
    # This used to run a SECOND isolated read here, which won over the row and therefore discarded any
    # manual correction - the reason "zero reviewer DOI corrections across 2,247 rows" was agreement in
    # appearance only. "-" means the document states none, and produces no prefix.
    doi_lead = _doi_lead(row["injury_date"])
    manual_tag, diag_tag = _row_tags(row)

    # Faithfulness verify pass (problem #3): audit the title AND the body against their source and,
    # ONLY when the pass flags issues, keep the corrected pair as verifiedTitle/verifiedText (the raw
    # summaryTitle/summaryText stay as the immutable model output). No issues -> both stay None, so
    # the summary is unchanged and unflagged. verify_summary is fail-safe (returns the originals on
    # any error). The title is audited because it is the first thing a client reads and it carries
    # dates and laterality that a body-only check never saw.
    verified_text = None
    verified_title = None
    verify_issues = None
    # Whether the audit actually RAN, which is not the same question as whether it was switched on.
    # Fail closed: anything that does not explicitly report success leaves this False.
    verify_ran = False
    if verify:
        verified_text, verified_title, verify_issues, verify_ran = _verified_outputs(
            audit_model, row, text, summary, title, doi_lead
        )

    # PARTIAL unreadable row: the body above was summarized from the pages that COULD be read, so
    # state the ones that could not. Without this a ten-page row that lost one page delivers a
    # summary of nine with nothing said, which is the same invisibility the whole-row notice removes.
    #
    # Appended AFTER the verify pass, deliberately. The audit checks the body against the SOURCE
    # TEXT, and this sentence is by definition not in that source - letting the audit see it invites
    # it to "correct" an unsupported claim, or to count it as a faithfulness issue and flag the row.
    # Applied to the verified body too, so the notice survives whichever body effective_text()
    # delivers, and after sentence_case_caps_runs so that transform never rewrites it.
    partial_notice, verified_text = _trailing_notices(
        verified_text, unreadable_pages, embedded_review_pages, page_offset
    )
    # Computed once, because the two are absent TOGETHER - a different fact from "not recorded",
    # which `verified` is what distinguishes.
    audit_model_used, audit_fingerprint = (
        (audit_model, fingerprint(VERIFY_PROMPT)) if verify else (None, None)
    )

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        # The body was cut off at the token budget: nothing is appended to the text (the report must
        # not carry a marker), but callers flag the row so the reviewer knows to check it.
        "truncated": truncated,
        "summaryText": f"{doi_lead}{summary}{partial_notice}",
        # The audit RAN, not "the audit was requested". Setting this from the `verify` setting meant a
        # row whose check threw or truncated was still stored claiming a faithfulness check had
        # happened - a false record on a medical summary, and one no later query could detect.
        "verified": verify_ran,
        "verifiedText": verified_text,
        "verifiedTitle": verified_title,
        "verifyIssues": verify_issues,
        # The exact model input, so callers can persist the fine-tuning pair.
        "sourceText": text,
        # PROVENANCE. Which models wrote this row and which prompt text they were given, so
        # "did the prompt change help" becomes a GROUP BY instead of dating rows against deploy
        # history. auditModel/auditFingerprint are None when the verify pass did not run - a
        # different fact from "not recorded", which `verified` distinguishes.
        "model": model,
        # Non-None only when the body fell back, and names what was ASKED for. `model` above already
        # names what answered, so a caller comparing the two sees the downgrade without a schema
        # change; `job.model` vs `summaries.model` shows the same thing after the fact.
        "bodyFallbackFrom": body_fallback_from,
        "titleModel": title_model,
        "auditModel": audit_model_used,
        # WHICH BACKEND answered, beside which model did. Read from the provider that actually served
        # the call rather than from config, so a row records what happened rather than what was
        # configured at the moment somebody later reads the settings.
        #
        # One value covers all three calls: body, title and audit are all the summarize stage, so
        # they resolve to the same backend. Whether each call RAN is already answered by its model
        # field being NULL or not, so a per-call backend would add no fact.
        "backend": _backend_name(),
        "promptFingerprint": prompt_fingerprint,
        "auditFingerprint": audit_fingerprint,
        # Pages the recognizer could not read. Non-empty here means this row WAS summarized, off the
        # pages that could be read, and carries the notice appended above - `noticeOnly` False is
        # what separates it from a row where nothing could be read at all.
        "unreadablePages": unreadable_pages,
        # Echoed back so the caller can set `summaries.embedded_review` from the same value that
        # produced the sentence, rather than re-deriving it or matching on the text.
        "embeddedReviewPages": embedded_review_pages,
        "noticeOnly": False,
    }
