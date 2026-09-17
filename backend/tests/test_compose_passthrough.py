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
