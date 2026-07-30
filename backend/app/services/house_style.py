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
    }
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
    """True for an allowlisted acronym, including a compound whose parts are all acronyms.

    The compound case matters: "EMG/NCS" is not in the list itself, and lowercasing half of it would
    read as a typo in a medico-legal document.
    """
    bare = word.rstrip(".,")
    if word in _ACRONYMS or bare in _ACRONYMS:
        return True
    pieces = [p for p in re.split(r"[/&]", bare) if p]
    return len(pieces) > 1 and all(p in _ACRONYMS for p in pieces)


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

    * a run of 2+ capitalised words containing an organisation marker becomes Title Case, because
      lowercasing a company or facility name is worse than leaving it shouting;
    * any other run of 2+ capitalised words becomes sentence case, capitalised only where the run
      begins a sentence so a mid-sentence run does not gain a stray capital;
    * a lone capitalised word of 4+ letters becomes Title case (LASIX -> Lasix).

    A run made entirely of acronyms is untouched. Never call this on a TITLE: the header line is ALL
    CAPS by convention in 812 of 813 measured human entries, so the transform would destroy the one
    place capitals are correct.
    """
    if not text:
        return text

    def replace_run(match: re.Match) -> str:
        run = match.group(0)
        words = [w for w in re.split(r"[ ,]+", run) if w]
        if all(_is_acronym(w) for w in words):
            return run  # e.g. "EMG NCS ECG" - nothing to fix
        proper = any(w.rstrip(".,") in _PROPER_NOUN_MARKERS for w in words)
        return _recase_run(
            run,
            start_of_sentence=bool(_SENTENCE_START.search(text[: match.start()])),
            title_case=proper,
        )

    out = _CAPS_RUN.sub(replace_run, text)

    def replace_word(match: re.Match) -> str:
        word = match.group(1)
        letters = re.sub(r"[^A-Z]", "", word)
        if _is_acronym(word) or len(letters) < _LONE_MIN_LETTERS:
            return word
        return word.capitalize()

    return _LONE_CAPS_WORD.sub(replace_word, out)
