"""Per-document summarization for the review flow.

Reproduces the legacy per-row behavior (same category prompts, title extraction, decorations).
Callers pass rows + the resolved prompt explicitly, so this service stays DB-free; when the
prompt is omitted it falls back to the hardcoded prompts.py dict (category_11 has none -> the
general prompt, avoiding the historical KeyError).

Model calls go through services.llm, so which vendor answers is a config value rather than an
import. This module no longer names an SDK.
"""

import io
import logging
import re

from pdf2image import convert_from_path

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services.house_style import sentence_case_caps_runs
from app.services.llm import ImagePart, TextPart, get_provider
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts
from app.services.summary_verify import verify_summary

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
    "the document, usually on the letterhead.\n"
    "3. DOCUMENT TYPE - what the document IS.\n"
    "   For a diagnostic study, name the study, never its class. Use the form "
    "<MODALITY> OF THE <SIDE IF STATED> <BODY PART> <CONTRAST STATUS IF STATED>, for example "
    '"MRI OF THE CERVICAL SPINE WITHOUT CONTRAST", "CT OF THE HEAD WITHOUT CONTRAST", '
    '"X-RAY OF THE LEFT WRIST", "EMG/NCS OF THE UPPER EXTREMITIES". Never "RADIOLOGY REPORT", '
    '"GENERAL RADIOLOGY PROCEDURE", or "DIAGNOSTIC REPORT". Ultrasound and mammogram studies '
    'are named organ-first, for example "THYROID ULTRASOUND".\n'
    "   A facility name is NOT a document type. If the top of the page carries only letterhead, "
    "read on for the study or report heading, which often sits above the findings.\n\n"
    "If an element is not stated in the document, omit that element and its separator. Do not "
    'write "UNKNOWN", "UNSPECIFIED", or any placeholder - an absent element is left out, the same '
    "way an absent point is left out of the summary body.\n\n"
    "Never include page numbers or page ranges, dates, or the patient's name. Return only the "
    "line, with no commentary."
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

# Depositions are the one measured exception: the human convention is one line per transcript page
# (median gap between referenced pages is 1, across 978 transitions), so a single-paragraph rule would
# destroy the format rather than tidy it. Stands alone - it used to be phrased as an exception to the
# paragraph rule, which is not sent to this category at all.
_F_DEPOSITION = (
    "- Summarize this transcript page by page, one line per page, each beginning with the page and "
    "line reference. Do NOT merge the lines into a paragraph: the page structure is what a reader "
    "relies on to locate testimony.\n"
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
    """
    # Any issue type outside the correction-only pair means the audit had a substantive reason to
    # restructure the body - an unsupported claim, a duplicated finding - so its rewrite stands.
    if not issue_types or not issue_types <= _CORRECTION_ONLY_ISSUES:
        return False
    return _bold_span_count(fixed) < _bold_span_count(raw)


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
# Every id the catalog ships. An id outside this set gets EVERY block (see build_preamble).
_KNOWN_CATEGORIES = (
    _EXAM_CATEGORIES
    | _VERDICT_CATEGORIES
    | _DEPOSITION_CATEGORIES
    | _EMBEDDED_REVIEW_CATEGORIES
    | frozenset({"4", "7", "8", "10", "11", "100"})
)


def build_preamble(category) -> str:
    """The shared rules that can bind on THIS category, assembled in a fixed order.

    Default is INCLUDE: an id the catalog does not ship yet (an admin can create one at any time via
    POST /admin/categories) receives every block except the deposition format, so a new category is
    never silently under-instructed. Only a KNOWN id has blocks withheld, and only where its documents
    structurally cannot contain the thing - a laboratory result has no range of motion, and a
    deposition transcript is not written as one paragraph.
    """
    cat = str(category)
    unknown = cat not in _KNOWN_CATEGORIES
    deposition = cat in _DEPOSITION_CATEGORIES
    exam = unknown or cat in _EXAM_CATEGORIES
    verdict = unknown or cat in _VERDICT_CATEGORIES
    embedded = unknown or cat in _EMBEDDED_REVIEW_CATEGORIES

    parts = [_FACTUALITY, _CONTENT_HEADER, _C_POINT_SCOPE]
    if exam:
        parts.append(_C_NORMAL_FINDINGS)
    if verdict:
        parts.append(_C_VERDICT)
    if embedded:
        parts.append(_C_EMBEDDED_REVIEW)
    parts.append(_C_CODES)
    if exam:
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


def model_for(kind, fallback):
    """The model that answers one summarize call: "body", "title" or "audit".

    On Gemini this returns ``fallback`` - the model the JOB was created with - so selecting a
    provider never silently changes which Gemini model runs, and a resumed job keeps using whatever
    it started on. On OpenAI the job's Gemini model name is meaningless, so the configured
    per-call-type model is used instead.
    """
    settings = get_settings()
    if settings.summary_provider == "openai":
        return settings.model_for(kind)
    return fallback


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
        max_output_tokens=max_output_tokens,
    )
    return response.text, response.truncated


def _page_image_parts(pdf_path, start, end):
    """Rasterize a sub-document's pages to lean JPEG image Parts for multimodal summarization.

    Capped at settings.summary_image_max_pages so a long sub-document cannot blow the payload; the
    full OCR text still covers every page. Rasterized one page at a time to cap peak memory.
    """
    settings = get_settings()
    last = min(int(end), int(start) + settings.summary_image_max_pages - 1)
    parts = []
    for page in range(int(start), last + 1):
        for image in convert_from_path(
            pdf_path, first_page=page, last_page=page, dpi=settings.summary_image_dpi
        ):
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
            parts.append(ImagePart(data=buffer.getvalue(), mime_type="image/jpeg"))
    return parts


def summarize_row(pdf_path, row, model=None, prompt=None, verify=None, standalone_studies=None):
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
    """
    settings = get_settings()
    model = model or settings.summary_model
    if verify is None:
        verify = settings.summary_verify
    if prompt is None:
        key = f"category_{int(row['category']):02d}" if row["category"] != "100" else "category_100"
        prompt = prompts.get(key, prompts["category_100"])
    # Prepend the shared rules that can bind on THIS category (applies to DB-resolved and fallback
    # prompts alike, and to any future category - build_preamble defaults an unknown id to everything).
    system_msg = build_preamble(row["category"]) + prompt
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

    # Depositions are summarized one line per transcript page, so this category needs to SEE where
    # each page ends. The stored text cannot be reused for them: page boundaries cannot be retrofitted
    # onto text that was already concatenated without them, so a marked re-extraction is the only
    # way. Confined to category 9 - markers in every category's input would push page numbers into
    # ordinary summaries and pollute the duplicate check's similarity scoring.
    deposition = str(row["category"]) == "9"
    # Reuse the duplicate check's OCR when it exists: it ran the SAME extraction over the SAME pages
    # and persisted it per row, so a second full pass is pure waste - on a 1500-page record that is
    # ~45 minutes of OCR done twice. Blank text is not reused, so a page whose OCR failed the first
    # time is retried here rather than being permanently condemned to EmptyExtractionError.
    text = "" if deposition else (row.get("source_text") or "").strip()
    if not text:
        pages = list(range(int(row["start"]), int(row["end"]) + 1))
        text = extract_text_from_selected_pages(pdf_path, pages, mark_pages=deposition)
    if not text.strip():
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
            body_contents = _page_image_parts(pdf_path, row["start"], row["end"]) + [
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
    summary, truncated = _generate(
        model_for("body", model),
        system_msg,
        body_contents,
        temperature=settings.summary_temperature,
        max_output_tokens=settings.summary_max_output_tokens,
    )
    title, _ = _generate(model_for("title", model), TITLE_PROMPT, text, temperature=0.0)
    # Deterministic capitalisation fix on the BODY only (the title is an ALL CAPS header by design).
    # The prompt rule and the audit rule both stay: this catches what they miss, which was 22% of
    # measured rows. Applied before the verify pass so the audit reads the text a reader will see.
    summary = sentence_case_caps_runs(summary)

    # The row IS the source of truth for the injury date. It was read once, per sub-document and in
    # isolation, at the END of segmentation (see segment_engine.run_segmentation), so a reviewer who
    # corrects it on the review page has that correction reach the delivered summary.
    #
    # This used to run a SECOND isolated read here, which won over the row and therefore discarded any
    # manual correction - the reason "zero reviewer DOI corrections across 2,247 rows" was agreement in
    # appearance only. "-" means the document states none, and produces no prefix.
    injury = row["injury_date"]
    # House grammar (see summary_doi): "**DOI**: <value>." - colon-space, period terminator. Stored
    # summaries written before 2026-07-29 carry the old "**DOI**:<value>," form and stay readable;
    # summary_doi.doi_prefix parses both.
    doi_final = "" if injury in ("", "-") else f"**DOI**: {injury}."
    diag_tag = " [Diagnostic Study]" if str(row["category"]) == "3" else ""
    manual_tag = "[ManualCheck] " if str(row["flag"]).strip().lower() == "x" else ""

    # Faithfulness verify pass (problem #3): audit the title AND the body against their source and,
    # ONLY when the pass flags issues, keep the corrected pair as verifiedTitle/verifiedText (the raw
    # summaryTitle/summaryText stay as the immutable model output). No issues -> both stay None, so
    # the summary is unchanged and unflagged. verify_summary is fail-safe (returns the originals on
    # any error). The title is audited because it is the first thing a client reads and it carries
    # dates and laterality that a body-only check never saw.
    verified_text = None
    verified_title = None
    verify_issues = None
    if verify:
        result = verify_summary(
            model_for("audit", model), text, summary, title=title, document_date=row.get("date")
        )
        if result["issues"]:
            issue_types = {
                str(issue.get("type") or "")
                for issue in result["issues"]
                if isinstance(issue, dict)
            }
            if _drops_required_headings(summary, result["fixed_text"], issue_types):
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
            else:
                # The audit may reintroduce capitals while fixing something else, so the transform runs
                # over its output too - the verified text is what effective_text() delivers.
                verified_text = f"{doi_final} {sentence_case_caps_runs(result['fixed_text'])}"
            verify_issues = result["issues"]
            # The title is corrected INDEPENDENTLY of the body, including when the body rewrite was
            # rejected above: effective_title() and effective_text() fall back separately, and a wrong
            # date or laterality in the title is exactly what this pass exists to catch.
            #
            # Decorated exactly like the stored title, so a verified title is a drop-in replacement
            # in every view; the export path strips the tags either way.
            fixed_title = (result.get("fixed_title") or "").strip()
            if fixed_title and fixed_title != title:
                verified_title = (
                    f"{manual_tag}{fixed_title}{diag_tag} (Pages {row['start']}-{row['end']})"
                )

    return {
        "summaryDate": row["date"],
        "summaryTitle": f"{manual_tag}{title}{diag_tag} (Pages {row['start']}-{row['end']})",
        "manualCheck": manual_tag,
        # The body was cut off at the token budget: nothing is appended to the text (the report must
        # not carry a marker), but callers flag the row so the reviewer knows to check it.
        "truncated": truncated,
        "summaryText": f"{doi_final} {summary}",
        "verified": bool(verify),
        "verifiedText": verified_text,
        "verifiedTitle": verified_title,
        "verifyIssues": verify_issues,
        # The exact model input, so callers can persist the fine-tuning pair.
        "sourceText": text,
    }
