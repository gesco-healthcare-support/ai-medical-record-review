"""Content fingerprints for the prompt text that produced a stored row.

A hand-maintained version constant does not survive contact with a repository that changes prompts in
a dozen PRs without bumping it - which is exactly what happened to ``gemini.PROMPT_VERSION``. A hash
over the prompt AS RESOLVED does, and it catches the case a hash over ``prompts.py`` alone would miss:
``catalog.get_prompt`` is DB-first with a code fallback, so an admin edit in the console must move the
fingerprint too.

Scope: this module hashes PROMPT TEXT only. Deterministic code (``house_style``, the per-row context
blocks in ``summarize_engine``) is not covered, because code belongs to a build identifier rather than
a prompt hash. The build-SHA half of the 2026-07-31 provenance plan is deliberately out of scope here,
so treat a fingerprint as "which prompt generation", not "which build".
"""

import hashlib


def fingerprint(*parts: str) -> str:
    """A short, stable digest of one or more prompt strings.

    Parts are separated by a NUL byte so that ("ab", "c") and ("a", "bc") cannot collide - without a
    separator, concatenation makes two different prompt sets hash identically. 12 hex characters is
    ~48 bits, far more than enough to tell apart the handful of prompt generations a project has,
    and short enough to read in a query result.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


def summary_prompt_fingerprint(preamble: str, prompt: str) -> str:
    """The fingerprint for ONE summary row: its shared preamble plus its category prompt.

    Deliberately excludes the per-row blocks ``summarize_row`` appends after this point (the record's
    other diagnostic studies, the document date). Those carry row and document DATA, so hashing them
    would give two rows on an identical prompt different fingerprints and make the cohort query this
    exists to enable useless.
    """
    return fingerprint(preamble, prompt)


def job_prompt_fingerprint(session, kind: str) -> str | None:
    """The fingerprint of the WHOLE prompt set in play for one job, or None if it cannot be built.

    Answers "which prompt generation was this run on" in a single column, which the per-row hashes
    cannot: a summarize job spans many categories and therefore many per-row fingerprints.

    Every prompt is resolved through ``catalog.get_prompt`` (DB-first), so an admin edit moves this.
    Imports are local because ``summarize_engine`` and ``catalog`` both sit downstream of this module
    and a top-level import would close a cycle.

    Fail-safe: returns None rather than raising. Provenance is a record, not a gate - a job must never
    fail to start because its stamp could not be computed.
    """
    try:
        from app.services import catalog
        from app.services.gemini import SEGMENTATION_PROMPT, SEGMENTATION_SYSTEM
        from app.services.summarize_engine import TITLE_PROMPT, build_preamble
        from app.services.summary_verify import VERIFY_PROMPT
        from app.services.taxonomy import CATEGORIES

        if kind == "segment":
            return fingerprint(SEGMENTATION_SYSTEM, SEGMENTATION_PROMPT)

        parts: list[str] = [TITLE_PROMPT, VERIFY_PROMPT]
        # Sorted so the hash depends on the prompt CONTENT, not on dict iteration order.
        for category_id in sorted(str(cid) for cid in CATEGORIES):
            parts.append(category_id)
            parts.append(build_preamble(category_id))
            parts.append(catalog.get_prompt(session, "summary", category_id) or "")
        return fingerprint(*parts)
    except Exception:  # noqa: BLE001 - never block a job on a provenance stamp
        return None
