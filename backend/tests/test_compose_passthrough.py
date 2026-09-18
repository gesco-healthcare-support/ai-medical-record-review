"""A setting the app refuses to boot over must be reachable from the environment.

THE TRAP THIS EXISTS FOR. `Settings` reads plain env vars, but a container only sees a variable
that `docker-compose.yml` names. So a key can exist in `app/config.py`, be documented in
`.env.example`, be set correctly in a real `.env`, and still never reach the app - silently, with
the default applied and nothing anywhere saying so. `docker-compose.yml:29-33` records finding this
the hard way on 2026-07-31, and `OPENAI_MAX_RPM`/`OPENAI_MAX_TPM` were a live instance of it.

WHY THESE SETTINGS AND NOT ALL 84. Most of `Settings` is deliberately not exposed - an operator has
no business retuning a thinking budget from an env file, and listing everything here would turn this
test into a second copy of the class that fails on every addition. The rule that picks this list is
narrower and it is a property, not a preference:

    a setting that a BOOT GUARD compares against another setting must be env-settable,
    because a guard that refuses to start over a value nobody can change is not a safety
    control - it is a wall.

Two of the entries below are compared by guards (`_validate_vllm_backend` checks the segment bound
against the image limit; `_validate_render_targets` checks each render target against the DPI
ceiling). The rest are here because they must match how a rented pod was SERVED, and the moment you
discover a mismatch is the moment a GPU is already running - the worst possible time for the fix to
be a code change and a deploy.

None of these was referenced in `docker-compose.yml` until 2026-09-16.
"""

import re
from pathlib import Path

import pytest

# Why each one must be settable without a code change. The reason travels with the name so a later
# reader can judge whether a new setting belongs here, rather than pattern-matching the list.
_MUST_REACH_A_CONTAINER = {
    "VLLM_SEGMENT_MAX_PAGES": "compared against the image limit by a boot guard",
    "VLLM_MAX_IMAGES_PER_PROMPT": "mirrors the pod's --limit-mm-per-prompt; boot guard reads it",
    "SUMMARY_IMAGE_MAX_PAGES": "also compared against that limit - four stages rasterise, not one",
    "SUMMARY_IMAGE_DPI": "the ceiling _validate_render_targets checks every target against",
    "DOI_IMAGE_LONG_EDGE_PX": "checked against that ceiling; unmeasured on a pod (issue #333)",
    "DEPOSITION_IMAGE_LONG_EDGE_PX": "checked against that ceiling; unmeasured (issue #333)",
    "DOI_MAX_OUTPUT_TOKENS": "counts thought tokens; a pod model reasons differently from Gemini",
    "DEPOSITION_MAX_OUTPUT_TOKENS": "counts thought tokens; same reason",
}


def _compose_text() -> str:
    path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    assert path.exists(), f"docker-compose.yml not found at {path}"
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("name,reason", sorted(_MUST_REACH_A_CONTAINER.items()))
def test_the_setting_is_named_in_compose(name, reason):
    """WHEN a pod-agreement setting exists, THE SYSTEM SHALL name it in docker-compose.yml.

    Asserted as a `${NAME:-default}` substitution rather than a bare mention, because a key that
    appears only in a COMMENT reads as present to a grep and passes nothing through to the container.
    That distinction is the entire failure mode.
    """
    assert re.search(rf"^\s+{name}:\s*\$\{{{name}:-", _compose_text(), re.M), (
        f"{name} is not passed through docker-compose.yml, so it can never reach a container "
        f"however it is set in .env - and it must be settable because {reason}."
    )


@pytest.mark.parametrize("name", sorted(_MUST_REACH_A_CONTAINER))
def test_the_setting_is_documented_in_env_example(name):
    """WHEN a pod-agreement setting exists, THE SYSTEM SHALL document it in .env.example.

    Compose passthrough makes a key WORK; .env.example is how anyone finds out it exists. A key that
    works but is undiscoverable is only half wired, and the person who needs it is standing at a
    running pod.
    """
    path = Path(__file__).resolve().parents[2] / ".env.example"
    assert re.search(rf"^{name}=", path.read_text(encoding="utf-8", errors="replace"), re.M), (
        f"{name} is missing from .env.example, so nobody will know it can be set."
    )


def test_the_mirrored_page_caps_match_their_modules():
    """The image guard reads two caps that live as module constants; this stops them drifting.

    `config` cannot import `services.summary_doi` or `services.deposition_pages` - those import
    `config`, so the dependency runs one way only - so the guard compares mirrored copies. A mirror
    that drifts still reads like a check and refuses, or fails to refuse, on the wrong number.

    Same pattern as the render-target tie test, and it is here for the same reason: a guard that
    describes a value it does not read can be silently wrong while looking correct.
    """
    from app.config import _DEPOSITION_IMAGE_CAP, _DOI_IMAGE_CAP
    from app.services import deposition_pages, summary_doi

    assert _DOI_IMAGE_CAP == summary_doi._MAX_PAGES, (
        "config's mirror of the DOI page cap has drifted from the module that owns it"
    )
    assert _DEPOSITION_IMAGE_CAP == deposition_pages._MAX_PAGES, (
        "config's mirror of the deposition page cap has drifted from the module that owns it"
    )


def test_every_stage_that_rasterises_is_covered_by_the_image_guard():
    """WHEN a service calls page_image_parts, THE SYSTEM SHALL have that stage in the image guard.

    THE CONTROL FOR THE WHOLE FIX. The guard covered segmentation alone and read as complete; the
    other three were safe only because the limit it compares against was unreachable in code. This
    counts the real call sites so adding a fifth rasterising service fails here rather than being
    discovered when a pod refuses it.
    """
    import re

    from app.config import _IMAGE_CAPPED_STAGES

    services = Path(__file__).resolve().parents[1] / "app" / "services"
    # `[ \t]+` rather than `\s+`: `\s` matches a NEWLINE, so `^\s+` happily spans a blank line and
    # matched `rasterise.py`'s own `def page_image_parts(` at column 0 - the module that DEFINES the
    # helper counted as a caller. Caught by this test failing on its first run, which is the only
    # reason it is not still wrong.
    callers = {
        path.stem
        for path in services.glob("*.py")
        if re.search(r"^[ \t]+.*page_image_parts\(", path.read_text(encoding="utf-8"), re.M)
    }
    # Module name -> the stage string the guard keys on. Named rather than derived, because the two
    # do not match ("summarize_engine" is the "summarize" stage) and guessing would be fragile.
    expected = {
        "summarize_engine": "summarize",
        "segment_engine": "segment",
        "summary_doi": "doi",
        "deposition_pages": "deposition",
    }
    assert callers == set(expected), (
        f"the services calling page_image_parts have changed: {sorted(callers)}. Update `expected` "
        "here AND Settings._stage_image_caps, or a stage sends images the pod may refuse."
    )
    # Read from the module tuple, NOT by constructing a Settings: building one here picks up
    # whatever env the run carries, which produced an intermittent foreign-key violation at this
    # test's setup before it was changed. `_stage_image_caps` is keyed on this tuple.
    assert set(_IMAGE_CAPPED_STAGES) == set(expected.values()), (
        f"the image guard covers {sorted(_IMAGE_CAPPED_STAGES)} but these stages rasterise: "
        f"{sorted(expected.values())}"
    )


def test_every_named_setting_actually_exists_on_settings():
    """The control. A list of names is only a guarantee while the names are real.

    Without this, renaming a field in `app/config.py` leaves this file asserting that compose passes
    through a setting that no longer exists - green, and proving nothing. Checked against the class
    rather than an import of each name, so a typo here fails rather than being silently skipped.
    """
    from app.config import Settings

    fields = set(Settings.model_fields)
    unknown = sorted(n for n in _MUST_REACH_A_CONTAINER if n.lower() not in fields)
    assert not unknown, (
        f"these names are asserted against compose but are not Settings fields: {unknown}. "
        "Either the field was renamed and this list was not, or the name is a typo."
    )


# --- The derived half. ------------------------------------------------------------------------
#
# The list above is hand-maintained, and that is defensible for it: "a boot guard compares this
# against another setting" is a property of the CODE, so a reader looking at a new guard can tell.
#
# This half covers a property of the COMMENT, and a hand list is how it went wrong. Four settings
# each documented themselves as revertable from the environment - "a regression reverts via env with
# no redeploy", "Env-toggle to revert to OCR-only", "Env-overridable so a regression reverts without
# a redeploy", "Env-overridable so a box can raise it without a redeploy" - and not one was named in
# `docker-compose.yml`, so every one of those sentences was false on every deployed box. It surfaced
# on 2026-09-18 mid-incident, when `SUMMARY_VERIFY=false` was the obvious containment for an audit
# that was destroying summaries, was already set in the box `.env`, and did nothing.
#
# DERIVED RATHER THAN LISTED, because the issue reporting it counted four by hand and there were
# five: `gemini_thinking_budget` says "set >0 or -1 (model-dynamic) via env to re-enable if a task
# regresses" and was missing too. Someone scanning eighty-six fields for a promise will miss one.
#
# The phrase set is the loose half and is meant to be. Adding a phrase costs nothing, and a promise
# worded some way not listed here is a FALSE NEGATIVE - this is a floor, not a proof that every
# claim in the file is honoured.
_ENV_TOGGLE_PHRASES = (
    "env-overridable",
    "env overridable",
    "env-toggle",
    "env toggle",
    "via env",
    "by env",
    "from env",
    "from .env",
    "without a redeploy",
    "with no redeploy",
    "without a deploy",
    "with no deploy",
    "without a rebuild",
    "with no rebuild",
)


def _config_lines() -> list[str]:
    path = Path(__file__).resolve().parents[1] / "app" / "config.py"
    return path.read_text(encoding="utf-8").splitlines()


def _settings_fields() -> list[tuple[str, int]]:
    """Every `Settings` field as (name, 1-based line of its annotation).

    Found with `ast` rather than by regex over the file, so a field is found by being a field.
    Shared by the two readers below so they cannot disagree about what a field is.
    """
    import ast

    path = Path(__file__).resolve().parents[1] / "app" / "config.py"
    settings_class = next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return [
        (node.target.id, node.lineno)
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]


def _field_comments() -> dict[str, str]:
    """Every field mapped to the comment block written directly above it.

    The block is the contiguous run of `#` lines immediately above the annotation, walked upwards,
    which is this file's convention throughout - and is why `summary_verify`'s four-line note and
    `audit_max_output_tokens`' twenty-line one both land on the right field. Adjacency is required
    rather than nearest-comment-wins, because skipping a blank line would hand a field with no
    comment of its own the PREVIOUS field's one. `test_no_comment_block_is_orphaned_from_its_field`
    is what makes the strict rule safe.
    """
    lines = _config_lines()
    comments: dict[str, str] = {}
    for name, lineno in _settings_fields():
        block: list[str] = []
        index = lineno - 2  # lineno is 1-based, so -2 is the line directly above the annotation
        while index >= 0 and lines[index].strip().startswith("#"):
            block.append(lines[index].strip().lstrip("#").strip())
            index -= 1
        comments[name] = " ".join(reversed(block))
    return comments


def _env_name(field: str) -> str:
    """The variable a deployment would actually set for `field`.

    NOT `field.upper()`. `model_config` sets no `env_prefix`, so that is right for eighty-five of
    the eighty-six - but `use_vertex` carries `validation_alias="GOOGLE_GENAI_USE_VERTEXAI"` and
    does not answer to its own name at all. Asking pydantic keeps this right for the next alias
    rather than for today's.
    """
    from app.config import Settings

    alias = Settings.model_fields[field].validation_alias
    return (alias if isinstance(alias, str) else field).upper()


def _settings_promising_an_env_toggle() -> list[str]:
    """Field names whose own comment advertises that they can be set from the environment."""
    return sorted(
        field
        for field, comment in _field_comments().items()
        if any(phrase in comment.lower() for phrase in _ENV_TOGGLE_PHRASES)
    )


_PROMISED = _settings_promising_an_env_toggle()


def test_the_comment_reader_still_finds_comments():
    """WHEN the derivation runs, THE SYSTEM SHALL prove it read something.

    A CONTROL, and the two parametrized tests below are worthless without it: pytest reports a
    parametrize over an empty list as nothing to run rather than as a failure, so a reformat of
    `app/config.py` that moved comments off the line above a field would disarm both while the
    suite stayed green.

    Asserts the reader's OUTPUT rather than naming protected settings, so this stays a control and
    does not quietly become the hand list the derivation exists to avoid. It catches the reader
    failing WHOLESALE; the orphan test below is what catches it failing for one field, which is the
    likelier accident and which this cannot see.
    """
    comments = _field_comments()
    assert len(comments) > 50, f"only {len(comments)} Settings fields parsed; the ast walk broke"
    documented = [field for field, comment in comments.items() if comment]
    assert len(documented) > 30, (
        f"only {len(documented)} of {len(comments)} fields came back with a comment block. The "
        "comment reader has stopped seeing comments, which disarms the tests below silently."
    )
    assert _PROMISED, (
        "no setting's comment advertises an env toggle, which has not been true of this file since "
        "the phrase set was written against it. The reader or the phrases have drifted."
    )


def test_no_comment_block_is_orphaned_from_its_field():
    """WHEN a comment block sits above a field, THE SYSTEM SHALL leave no blank line between them.

    THE FAILURE THE AGGREGATE CONTROL ABOVE CANNOT SEE, found by running it: inserting one blank
    line above `summary_verify` does not make anything fail - it makes that field drop out of
    `_PROMISED`, so its test stops being generated and the run goes from nine passes to eight. A
    silently smaller guard looks exactly like a passing one.

    So the adjacency the reader depends on is enforced here rather than hoped for. Zero fields
    violate it today; the alternative - letting the reader skip blank lines - is worse, because
    then a field with no comment of its own would inherit the previous field's and could be
    protected, or unprotected, on the strength of a sentence about something else.

    A deliberate section header over a GROUP of fields would fail this. That is the right outcome
    to have to argue with: this file has none, and one would break the reader for the first field
    under it.
    """
    lines = _config_lines()
    orphaned = []
    for name, lineno in _settings_fields():
        index = lineno - 2
        if index < 0 or lines[index].strip().startswith("#"):
            continue  # no gap: either adjacent comment, or code/nothing above
        above = index
        while above >= 0 and not lines[above].strip():
            above -= 1
        if above >= 0 and above != index and lines[above].strip().startswith("#"):
            orphaned.append(f"{name} (line {lineno})")
    assert not orphaned, (
        f"a comment block is separated from its field by a blank line: {orphaned}. The reader that "
        "decides which settings promise an env toggle requires adjacency, so that field is now "
        "silently outside every check in this file rather than failing one."
    )


@pytest.mark.parametrize("field", _PROMISED)
def test_a_setting_that_promises_an_env_toggle_can_reach_a_container(field):
    """WHEN a comment says a setting is env-settable, THE SYSTEM SHALL name it in compose.

    The promise and the passthrough live in two files, are written at different times, and only the
    passthrough is load-bearing. This fails on the sentence, so the sentence stops being free.

    Asserted as a `${NAME:-default}` substitution rather than a bare mention: a key named only in a
    comment reads as present to a grep and passes nothing to a container, and `OMP_THREAD_LIMIT`
    shows the other shape - passed, but hardcoded, so `.env` cannot reach it either.
    """
    name = _env_name(field)
    assert re.search(rf"^\s+{name}:\s*\$\{{{name}:-", _compose_text(), re.M), (
        f"app/config.py tells a reader that `{field}` can be set from the environment, but "
        f"{name} is not passed through docker-compose.yml - so on a deployed box it cannot be, "
        "whatever .env says. Add it to the x-backend-env anchor, or stop promising it."
    )


@pytest.mark.parametrize("field", _PROMISED)
def test_the_compose_default_matches_the_config_default(field):
    """WHEN compose names a setting, THE SYSTEM SHALL repeat config.py's default exactly.

    THE MIRROR IMAGE, and the more expensive direction. Naming a key in compose makes THAT the
    value a container reads and `app/config.py` merely documentation - so a typo in a fallback
    changes production silently, and editing the code default afterwards changes nothing at all.
    `DUPE_SIMILARITY_OVERRIDE` is this tree's worked example: config said 0.99, compose said 0.90,
    and every container served 0.90 for three weeks while the code and the analysis said 0.99.

    Compared through the field's own annotation, so "true" is read as a bool and "8192" as an int.
    Comparing raw strings would fail on every non-string setting, and comparing `str(default)`
    would pass "0.0" against 0 by accident.
    """
    from pydantic import TypeAdapter

    from app.config import Settings

    name = _env_name(field)
    match = re.search(rf"^\s+{name}:\s*\$\{{{name}:-(.*?)\}}\s*$", _compose_text(), re.M)
    assert match, f"{name} is not passed through docker-compose.yml"  # the test above says why

    info = Settings.model_fields[field]
    composed = TypeAdapter(info.annotation).validate_python(match.group(1))
    assert composed == info.default, (
        f"docker-compose.yml defaults {name} to {composed!r} but app/config.py defaults "
        f"`{field}` to {info.default!r}. Compose wins inside a container, so the code default is "
        "already dead and every box is running the compose one."
    )
