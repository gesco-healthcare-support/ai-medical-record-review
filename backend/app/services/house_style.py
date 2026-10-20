"""Deterministic house-style fixes applied to a summary body after the model has written it.

Some house rules are judgement calls (which points belong, whether a review restates a study) and
belong to the model and its audit. Capitalisation is not one of them: turning a run of capitals into
ordinary case is a mechanical transform, and a mechanical transform is right every time. Measured on
2026-07-30, 22% of stored summaries still carried a 3+-word all-caps run - and 486 all-caps words that
are not acronyms - after BOTH the generation rule and the audit rule had had a go at it.

Deliberately NOT here: stripping ICD/CPT codes. The prompt rule already took those from 305 of 833
rows (36%) to 1 of 124, so a stripper would be dead code - and rewriting a sentence around a removed
code is exactly the kind of language damage a regex cannot judge.
"""

import re

# Uppercase tokens that are genuine acronyms or initialisms and must survive untouched. Sourced from
# the categories' own vocabulary plus the measured offender list; AROM and LEFS are here because they
# appeared in real summaries and would otherwise be rendered "Arom" and "Lefs".
_ACRONYMS = frozenset(
    {
        "MRI",
        "MRA",
        "CT",
        "CTA",
        "EMG",
        "NCS",
        "NCV",
        "ECG",
        "EKG",
        "EEG",
        "XR",
        "DEXA",
        "APAP",
        "CPAP",
        "EGD",
        "GI",
        "IV",
        "PO",
        "QD",
        "BID",
        "TID",
        "PRN",
        "NSAID",
        "NSAIDS",
        "QME",
        "AME",
        "PQME",
        "PTP",
        "RFA",
        "WCAB",
        "MMI",
        "TTD",
        "TPD",
        "PD",
        "WPI",
        "AMA",
        "ADL",
        "ADLS",
        "HPI",
        "PMH",
        "PSH",
        "ROS",
        "PE",
        "ROM",
        "AROM",
        "PROM",
        "LEFS",
        "SOAP",
        "ICD",
        "CPT",
        "DOI",
        "MOI",
        "CC",
        "WNL",
        "PT",
        "OT",
        "DC",
        "EMS",
        "TENS",
        "MRR",
        "PIP",
        "DIP",
        "MCP",
        "FDS",
        "FDP",
        "TFCC",
        "SLAP",
        "ACL",
        "PCL",
        "MCL",
        "LCL",
        "CBC",
        "CMP",
        "BMP",
        "A1C",
        "TSH",
        "PSA",
        "UA",
        "HDL",
        "LDL",
        "BMI",
        "PR-2",
        "PR-4",
        "PR-1",
        "P&S",
        "H&P",
        "TENS",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
        "S1",
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "C7",
        "T1",
        "T12",
        # Added 2026-10-20 from the capitals human writers keep in their summaries (23,744 eData
        # entries, every token here seen in five or more deliverables) and a reviewer's report.
        # Only tokens WITH a vowel need an entry: _is_acronym already keeps any token carrying a
        # digit or no vowel (PTSD, LLC, TTP, HGB, L4-L5, COVID-19).
        "GERD",
        "COPD",
        "COVID",
        "MRSA",
        "ATFL",
        "AOE",
        "COE",
        "ORIF",
        "ACDF",
        "HEP",
        "SI",
        "AC",
        "ESI",
        "LESI",
        "CESI",
        "TFESI",
        "ILESI",
        "TPI",
        "TPIS",
        "ESWT",
        "ECSWT",
        "FABER",
        "FABERE",
        "FADIR",
        "PSIS",
        "DROM",
        "STIR",
        "LVEF",
        "SSEP",
        "RUE",
        "LUE",
        "RLE",
        "LLE",
        "BUE",
        "BLE",
        "BI-RADS",
        "DISI",
        "OTC",
        "ASA",
        "SSRI",
        "IM",
        "ALT",
        "AST",
        "SGOT",
        "BUN",
        "POCT",
        "TDAP",
        "ETOH",
        "MVA",
        "ER",
        "ED",
        "PACU",
        "STAT",
        "ASAP",
        "II",
        "III",
        "DASH",
        "MTU",
        "MTUS",
        "MPN",
        "MPNS",
        "ACOEM",
        "CURES",
        "FMLA",
        "DRUJ",
        "AOE/COE",
        "UCLA",
        "SCOI",
        "IHSS",
        "HVAC",
        "LAPD",
        "LAUSD",
        "LACMTA",
        "MD",
        "PA",
        "PA-C",
        "NP",
        "PHD",
        "PSYD",
        "DPM",
        "RN",
        "LVN",
        "LCSW",
    }
)

# Two-letter state codes, which follow a city in an address ("San Diego, CA"). Left out on purpose:
# the ones that are also English words (IN, OR, ME, HI, OK, OH), CO, which names a company, and MS,
# which is far likelier "Ms." here than Mississippi.
_STATE_CODES = frozenset(
    "AL AK AZ AR CA CT DE DC FL GA IA ID IL KS KY LA MA MD MI MN MO MT NC ND NE NH NJ NM NV NY "
    "PA RI SC SD TN TX UT VA VT WA WI WV WY".split()
)

# A list can never know every acronym, so two shapes keep their capitals without an entry.
# Measured 2026-10-20 over 23,744 human-written summary entries: this pass changed 11.9% of the
# capital-letter tokens human writers use, and the commonest were acronyms and lab names it did not
# know (GERD, TTP, PTSD, HGB, RBC, ORIF) and spinal levels inside a run ("L4-L5" became "l4-l5"). A
# reviewer reported "Ptsd" in a delivered summary and named PTSD, MTU, LLC and CA.
# - A token carrying a digit is never a shouted word: "L4-L5", "COVID-19", "HGBA1C", "T4".
# - A token with no vowel (Y counts as one) is never a shouted word: "PTSD", "LLC", "TTP", "HGB".
#   The exceptions are ordinary words written short - titles, units and street abbreviations -
#   which recase as they always did.
_VOWELS = frozenset("AEIOUY")
# A run that ends in a comma and a state code is a town ("SAN DIEGO, CA") and is title-cased.
_PLACE_END = re.compile(r",\s*([A-Z]{2})\.?$")
_SHORT_WORDS = frozenset(
    "DR MR MRS MS JR SR ST RD LN BLVD MG MCG ML KG LB LBS CM MM HR HRS".split()
)

# Words that mark a run as an organisation or facility name rather than a clinical phrase, so it is
# TITLE-cased ("Cedar Ridge Logistics, Inc.") instead of sentence-cased ("cedar ridge logistics").
# Lowercasing a company name is a worse error than leaving it in capitals.
_PROPER_NOUN_MARKERS = frozenset(
    {
        "INC",
        "INC.",
        "LLC",
        "LLP",
        "LTD",
        "CORP",
        "CORP.",
        "CO",
        "CO.",
        "COMPANY",
        "GROUP",
        "MEDICAL",
        "IMAGING",
        "HOSPITAL",
        "CLINIC",
        "CENTER",
        "CENTRE",
        "ASSOCIATES",
        "PARTNERS",
        "SERVICES",
        "SYSTEMS",
        "HEALTH",
        "HEALTHCARE",
        "ORTHOPEDIC",
        "ORTHOPAEDIC",
        "SURGICAL",
        "LABORATORY",
        "LABS",
        "DIAGNOSTICS",
        "RADIOLOGY",
        "PHYSICAL",
        "THERAPY",
        "REHAB",
        "UNIFIED",
        "DISTRICT",
        "UNIVERSITY",
        "COUNTY",
        "CITY",
        "STATE",
        "GRP",
        "GRP.",
        "CONTRACTING",
        "CONSTRUCTION",
        "LOGISTICS",
        "FOODS",
        "FARMS",
        "TRUCKING",
        "STAFFING",
    }
)

# A word made of capitals: 2+ characters so a single initial is never a word on its own, digits
# included so "PR-2", "L4" and "A1C" stay whole tokens.
_CAPS_WORD = r"[A-Z][A-Z0-9&/.\-]+"
# Two or more in a row. Two is safe, not three, because a single capital letter does not match
# _CAPS_WORD at all - so "VITAMIN D" is a ONE-token run and is handled by the lone-word rule, and the
# pairs this catches ("CT SCAN", "GENERAL LABORER", "MRI LUMBAR") all want fixing.
_CAPS_RUN = re.compile(rf"{_CAPS_WORD}(?:[ ,]+{_CAPS_WORD})+")
# A lone all-caps token, matched as a WHOLE token so a hyphenated compound is not cut in half. The
# trailing guard excludes a following letter, digit, & or / (so "EMG" inside "EMG/NCS" is not matched
# on its own) but ALLOWS a following period, or a word ending a sentence would never be reached.
_LONE_CAPS_WORD = re.compile(rf"(?<![A-Za-z0-9&/.\-])({_CAPS_WORD})(?![A-Za-z0-9&/\-])")
# Below this length a lone capitalised token is assumed to be an acronym even when the allowlist does
# not know it: the measured offenders (VITAMIN, LASIX, LIVER, KEFLEX) are all longer.
_LONE_MIN_LETTERS = 4
# What the run follows when it starts a sentence rather than sitting inside one.
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]|\*\*)\s*$")


def _is_acronym(word: str) -> bool:
    """True for a token that keeps its capitals: an allowlisted acronym or state code, a token
    carrying a digit or no vowel, or a compound whose parts all qualify.

    The compound case matters: "EMG/NCS" is not in the list itself, and lowercasing half of it would
    read as a typo in a medico-legal document.
    """
    bare = word.rstrip(".,")
    if word in _ACRONYMS or bare in _ACRONYMS or bare in _STATE_CODES:
        return True
    pieces = [p for p in re.split(r"[/&]", bare) if p]
    if len(pieces) > 1:
        return all(_is_acronym(p) for p in pieces)
    if any(ch.isdigit() for ch in bare):
        return True
    letters = re.sub(r"[^A-Z]", "", bare)
    return bool(letters) and not set(letters) & _VOWELS and bare not in _SHORT_WORDS


def _recase_run(run: str, *, start_of_sentence: bool, title_case: bool) -> str:
    """Rewrite one run, keeping any token that is a real acronym and preserving the separators."""
    parts = re.split(r"([ ,]+)", run)
    out, first_word_done = [], False
    for part in parts:
        if not part or re.fullmatch(r"[ ,]+", part):
            out.append(part)
            continue
        if _is_acronym(part):
            out.append(part)
            first_word_done = True
            continue
        word = part.capitalize() if title_case else part.lower()
        if not title_case and not first_word_done and start_of_sentence:
            word = word[:1].upper() + word[1:]
        out.append(word)
        first_word_done = True
    return "".join(out)


def sentence_case_caps_runs(text: str) -> str:
    """Rewrite all-caps text in ``text`` as ordinary case, leaving genuine acronyms alone.

    Three behaviours, in order of precedence:

    * a run of 2+ capitalised words containing an organisation marker, or ending in a comma and a
      state code ("SAN DIEGO, CA" is a place), becomes Title Case, because lowercasing a company,
      facility or town is worse than leaving it shouting;
    * any other run of 2+ capitalised words becomes sentence case, capitalised only where the run
      begins a sentence so a mid-sentence run does not gain a stray capital;
    * a lone capitalised word of 4+ letters becomes Title case (LASIX -> Lasix).

    An acronym keeps its capitals wherever it stands (see `_is_acronym`: the allowlist, a state
    code, a token with a digit or no vowel), and a run made entirely of acronyms is untouched.

    Never call this on a TITLE: the header line is ALL CAPS by convention in 812 of 813 measured
    human entries, so the transform would destroy the one place capitals are correct.
    """
    if not text:
        return text

    def replace_run(match: re.Match) -> str:
        run = match.group(0)
        words = [w for w in re.split(r"[ ,]+", run) if w]
        if all(_is_acronym(w) for w in words):
            return run  # e.g. "EMG NCS ECG" - nothing to fix
        proper = any(w.rstrip(".,") in _PROPER_NOUN_MARKERS for w in words)
        place = _PLACE_END.search(run)
        return _recase_run(
            run,
            start_of_sentence=bool(_SENTENCE_START.search(text[: match.start()])),
            title_case=proper or bool(place and place.group(1) in _STATE_CODES),
        )

    out = _CAPS_RUN.sub(replace_run, text)

    def replace_word(match: re.Match) -> str:
        word = match.group(1)
        letters = re.sub(r"[^A-Z]", "", word)
        if _is_acronym(word) or len(letters) < _LONE_MIN_LETTERS:
            return word
        return word.capitalize()

    return _LONE_CAPS_WORD.sub(replace_word, out)


# ONE paragraph, deterministically. The prompt already says so (`_F_ONE_PARAGRAPH`) and the model
# still breaks it: over the 30 days to 2026-09-24 on the live box, 83 delivered summaries outside the
# deposition category carried line breaks and 20 an EMPTY label - a heading followed straight by the
# next one, `**Objective Findings**:` then a line break into `**Range of Motion**:`. Adam Flake
# reported one of them as "some strange formatting happening in the 06/18/26 PT evaluation report",
# and the reviewer's own correction of it was exactly this: the same content, run together inline,
# with the empty heading gone.
#
# Line by line rather than one substitution, so each join can choose its punctuation: a line that
# already ends a sentence or a clause joins with a space, one that does not gets "; " (". " before a
# heading) so two list items do not run into one clause. A leading list marker (`- `, `* `, a bullet,
# `1. `) is dropped with its line break. NOT for depositions, which are grouped by page on purpose -
# the caller decides.
_LIST_MARKER = re.compile(r"(?:[-*•]|\d{1,2}[.)])[ \t]{1,4}")
# An EMPTY heading is a LABEL - a bold span marked as one by its colon, `**X**:` or `**X:**` -
# followed by nothing but another label or the end. Both halves are load-bearing, and the first
# version had neither: it treated ANY bold span at the end as a heading, and deleted the label in
# front of any bold span. Adrian reproduced both on review (#396), measured over 4,570 stored
# summaries: 175 would have lost words and 28 a key label entirely -
# `**Work Status**: **Modified duty.**` lost its work status, `**Impression**: **Normal study.**`
# the whole summary. A bold span without a colon is content, never a heading, and a label followed
# by bold CONTENT is not empty.
_LABEL = r"\*\*[^*\n]{1,60}(?:\*\*:|:\*\*)"
_EMPTY_LABEL = re.compile("(" + _LABEL + r")[ \t]{0,8}(?=(" + _LABEL + r")|$)")
_CLAUSE_END = ".:;,!?"
# ...and a label followed by a label is not always vacuous (#407). In a summary covering several
# studies, `**MRI of the lumbar spine (05/15/2025):** **Findings**: ...` NAMES the study the next
# heading belongs to; removing it ran two studies' findings together and lost both names and dates.
# So a label is empty only when it carries nothing: no digit (a date, a level, a study number) and
# not followed by a result heading, which is what a study heading introduces. The reviewers' case,
# `**Objective Findings**:` straight into `**Range of Motion**:`, is still removed.
_RESULT_LABEL = re.compile(
    r"\*\*\s*(?:findings?|impressions?|conclusions?|interpretation|results?)\b", re.I
)
_DIGIT = re.compile(r"\d")


def _vacuous(label: re.Match) -> bool:
    """Is this label-followed-by-a-label (or by the end) a heading that introduces nothing?"""
    if _DIGIT.search(label.group(1)):
        return False
    return not _RESULT_LABEL.match(label.group(2) or "")


def _without_empty_labels(text: str) -> str:
    return _EMPTY_LABEL.sub(lambda m: "" if _vacuous(m) else m.group(0), text)


def one_paragraph(text: str) -> str:
    """``text`` as a single paragraph, with any heading that introduces nothing removed.

    Returns ``text`` unchanged when it is already one paragraph with no empty heading, so a
    conforming summary passes through byte for byte."""
    if not text or ("\n" not in text and not any(map(_vacuous, _EMPTY_LABEL.finditer(text)))):
        return text
    lines = [ln.strip() for ln in text.strip().splitlines()]
    lines = [_LIST_MARKER.sub("", ln, count=1) if _LIST_MARKER.match(ln) else ln for ln in lines]
    flat = ""
    for line in (ln for ln in lines if ln):
        if not flat:
            flat = line
        elif flat[-1] in _CLAUSE_END or flat.endswith("**"):
            flat = f"{flat} {line}"
        elif line.startswith("**"):
            flat = f"{flat}. {line}"
        else:
            flat = f"{flat}; {line}"
    flat = _without_empty_labels(flat)
    return re.sub(r"[ \t]{2,}", " ", flat).strip()
