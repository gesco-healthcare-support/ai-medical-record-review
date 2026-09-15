"""Startup guards for the OpenAI provider.

These fail at BOOT rather than on the first summary on purpose: a worker that starts and then errors
per row burns a job and leaves the reviewer with a half-processed document.

The ZDR guard is the one that matters most. A signed BAA does not by itself permit sending PHI to
OpenAI - Zero Data Retention (or Modified Abuse Monitoring / Eyes Off) must ALSO be approved on the
organization. The flag is a human acknowledgement that someone checked, so a future box cannot start
sending medical records to an org whose retention setting nobody looked at.
"""

import pytest

from app.config import Settings

# Settings is a pydantic-settings model: it reads the ENVIRONMENT, and uppercase constructor kwargs
# are silently ignored as extras. So these must be set as real env vars or the guards never fire -
# which is exactly how an earlier version of this file passed while testing nothing.
_BASE = {
    "SECRET_KEY": "x" * 32,
    "SECURITY_PASSWORD_SALT": "y" * 16,
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "ENVIRONMENT": "dev",
}
_PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "SUMMARY_BODY_MODEL",
    "SUMMARY_TITLE_MODEL",
    "AUDIT_MODEL",
    "OPENAI_ZDR_ACKNOWLEDGED",
    "SUMMARY_PROVIDER",
    "SUMMARY_MODEL",
    # The vLLM/backend keys belong here for the same reason as the seven above: a value left in a
    # developer's .env would otherwise satisfy a guard these tests are trying to make fire.
    "LLM_BACKEND",
    "LLM_BACKEND_OVERRIDES",
    "VLLM_BASE_URL",
    "VLLM_API_KEY",
    "VLLM_MODEL",
    "VLLM_APPROVED_ORIGINS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a known env, so a developer's .env cannot mask a guard."""
    for name in _PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in _BASE.items():
        monkeypatch.setenv(name, value)


def _settings(monkeypatch, **overrides):
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    return Settings()  # type: ignore[call-arg]


def test_gemini_provider_needs_none_of_the_openai_keys(monkeypatch):
    # The default path must stay unaffected by anything added for OpenAI.
    assert _settings(monkeypatch, SUMMARY_PROVIDER="gemini").summary_provider == "gemini"


@pytest.mark.parametrize(
    "missing",
    ["OPENAI_API_KEY", "SUMMARY_BODY_MODEL", "SUMMARY_TITLE_MODEL", "AUDIT_MODEL"],
)
def test_openai_provider_refuses_to_start_without_each_required_key(missing, monkeypatch):
    keys = {
        "OPENAI_API_KEY": "sk-test",
        "SUMMARY_BODY_MODEL": "a",
        "SUMMARY_TITLE_MODEL": "b",
        "AUDIT_MODEL": "c",
    }
    keys.pop(missing)
    with pytest.raises(RuntimeError, match=missing):
        _settings(monkeypatch, SUMMARY_PROVIDER="openai", **keys)


def test_the_error_says_why_there_is_no_default_model(monkeypatch):
    with pytest.raises(RuntimeError, match="no default model on purpose"):
        _settings(monkeypatch, SUMMARY_PROVIDER="openai", OPENAI_API_KEY="sk-test")


def test_production_refuses_openai_without_the_zdr_acknowledgement(monkeypatch):
    with pytest.raises(RuntimeError, match="OPENAI_ZDR_ACKNOWLEDGED"):
        _settings(
            monkeypatch,
            SUMMARY_PROVIDER="openai",
            ENVIRONMENT="prod",
            OPENAI_API_KEY="sk-test",
            SUMMARY_BODY_MODEL="a",
            SUMMARY_TITLE_MODEL="b",
            AUDIT_MODEL="c",
        )


def test_production_accepts_openai_once_zdr_is_acknowledged(monkeypatch):
    settings = _settings(
        monkeypatch,
        SUMMARY_PROVIDER="openai",
        ENVIRONMENT="prod",
        OPENAI_API_KEY="sk-test",
        SUMMARY_BODY_MODEL="a",
        SUMMARY_TITLE_MODEL="b",
        AUDIT_MODEL="c",
        OPENAI_ZDR_ACKNOWLEDGED="true",
    )
    assert settings.summary_provider == "openai"


def test_model_for_returns_the_configured_model_per_call_type(monkeypatch):
    settings = _settings(
        monkeypatch,
        SUMMARY_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        SUMMARY_BODY_MODEL="body-model",
        SUMMARY_TITLE_MODEL="title-model",
        AUDIT_MODEL="audit-model",
    )
    assert settings.model_for("body") == "body-model"
    assert settings.model_for("title") == "title-model"
    assert settings.model_for("audit") == "audit-model"


def test_gemini_defaults_the_cheap_calls_to_flash(monkeypatch):
    # WHEN the provider is gemini and no per-call-type key is set, THE SYSTEM SHALL keep the body on
    # summary_model and step the title and audit down to flash. That began as the 3-calls-of-pro to 1
    # reduction; before 2026-08-06 model_for returned summary_model for all three and the keys were
    # inert on this path. The body default became 3.5-flash on 2026-08-14 (see config._derive), so
    # this now pins the SPLIT rather than the saving - title and audit must not follow the body model.
    settings = _settings(monkeypatch, SUMMARY_PROVIDER="gemini")
    assert settings.model_for("body") == settings.summary_model == "gemini-3.5-flash"
    assert settings.model_for("title") == "gemini-2.5-flash"
    assert settings.model_for("audit") == "gemini-2.5-flash"


def test_gemini_per_call_keys_are_honoured_when_set(monkeypatch):
    # WHEN a per-call-type key IS set on the gemini path, THE SYSTEM SHALL use it rather than the
    # default - the whole point of the change is that these knobs do something here now.
    settings = _settings(
        monkeypatch,
        SUMMARY_PROVIDER="gemini",
        SUMMARY_BODY_MODEL="body-override",
        SUMMARY_TITLE_MODEL="title-override",
        AUDIT_MODEL="audit-override",
    )
    assert settings.model_for("body") == "body-override"
    assert settings.model_for("title") == "title-override"
    assert settings.model_for("audit") == "audit-override"


def test_provider_name_is_normalised(monkeypatch):
    assert _settings(monkeypatch, SUMMARY_PROVIDER="  GEMINI  ").summary_provider == "gemini"


# --- the production Vertex guard ------------------------------------------------------------------
#
# CHARACTERIZATION. These pin config._derive's prod check (the `environment == "prod" and not
# use_vertex` raise) BEFORE it is rewritten to check an approved DESTINATION rather than a boolean.
#
# Nothing covered it until now, and the gap was invisible: `_BASE` sets GOOGLE_GENAI_USE_VERTEXAI
# true for the OpenAI tests above, so every one of them satisfies this guard incidentally while
# asserting nothing about it. A guard that is only ever satisfied by accident is a guard that can be
# deleted without a single test going red - which is the failure these three exist to prevent.
#
# This is the highest-stakes control in the file: it is the reason a production box cannot send PHI
# to the non-BAA Developer API endpoint.


def test_production_refuses_to_start_without_vertex(monkeypatch):
    # PHI may only go to the BAA-covered Vertex endpoint. The Developer API is not covered, so a
    # prod box that has not selected Vertex must not boot at all.
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_USE_VERTEXAI"):
        _settings(monkeypatch, ENVIRONMENT="prod", GOOGLE_GENAI_USE_VERTEXAI="false")


def test_the_production_vertex_error_says_it_is_about_phi(monkeypatch):
    # The message has to name the REASON, not just the key. Someone hitting this at deploy time
    # needs to know it is a compliance control and not a misconfiguration to be worked around.
    with pytest.raises(RuntimeError, match="BAA-covered Vertex endpoint"):
        _settings(monkeypatch, ENVIRONMENT="prod", GOOGLE_GENAI_USE_VERTEXAI="false")


def test_production_starts_with_vertex(monkeypatch):
    settings = _settings(monkeypatch, ENVIRONMENT="prod", GOOGLE_GENAI_USE_VERTEXAI="true")
    assert settings.use_vertex is True


def test_the_vertex_guard_is_production_only(monkeypatch):
    # Dev and test boxes run against the Developer API deliberately - they have no PHI. Pinning this
    # direction too, because a "safer" guard that fired everywhere would break every local stack and
    # be reverted wholesale, taking the prod protection with it.
    settings = _settings(monkeypatch, ENVIRONMENT="dev", GOOGLE_GENAI_USE_VERTEXAI="false")
    assert settings.use_vertex is False


# --- backend selection ----------------------------------------------------------------------------

_VLLM = {"VLLM_BASE_URL": "http://127.0.0.1:8000/v1", "VLLM_MODEL": "Qwen/Qwen3.6-35B-A3B-FP8"}


def test_every_stage_follows_the_global_backend_by_default(monkeypatch):
    settings = _settings(monkeypatch, LLM_BACKEND="gemini")
    assert {settings.backend_for(s) for s in ("summarize", "segment", "classify")} == {"gemini"}


def test_a_per_stage_override_wins_over_the_global_backend(monkeypatch):
    # The whole reason overrides exist: a split outcome is the likely one. On the 2026-09-11 gate
    # Qwen matched Gemini on segmentation but inverted the error direction and trailed on
    # categorization, so "move everything or nothing" would turn a per-stage call into one bet.
    settings = _settings(
        monkeypatch, LLM_BACKEND="vllm", LLM_BACKEND_OVERRIDES="classify=gemini", **_VLLM
    )
    assert settings.backend_for("classify") == "gemini"
    assert settings.backend_for("summarize") == "vllm"


def test_resolved_backends_sees_a_backend_reachable_only_through_an_override(monkeypatch):
    # A guard reading llm_backend alone would never notice this one, which is the hole
    # resolved_backends exists to close.
    settings = _settings(
        monkeypatch, LLM_BACKEND="gemini", LLM_BACKEND_OVERRIDES="segment=vllm", **_VLLM
    )
    assert settings.resolved_backends() == {"gemini", "vllm"}


def test_an_unknown_stage_in_the_overrides_refuses_to_start(monkeypatch):
    # Ignoring a typo would leave that stage on its old backend while the operator believes it moved.
    with pytest.raises(RuntimeError, match="does not name a known stage"):
        _settings(monkeypatch, LLM_BACKEND_OVERRIDES="sumarize=vllm")


def test_an_override_without_an_equals_sign_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="does not name a known stage"):
        _settings(monkeypatch, LLM_BACKEND_OVERRIDES="segment")


def test_an_unknown_backend_in_the_overrides_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="unknown backend"):
        _settings(monkeypatch, LLM_BACKEND_OVERRIDES="segment=qwen")


def test_an_unknown_global_backend_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="is not a known backend"):
        _settings(monkeypatch, LLM_BACKEND="qwen")


def test_thinking_budgets_are_unchanged_per_stage(monkeypatch):
    # Characterization. These three values were each set for their own measured reason, so the
    # resolver must reproduce today's mapping exactly rather than tidy it into one number.
    settings = _settings(monkeypatch)
    assert settings.thinking_for("segment") == settings.segment_thinking_budget
    for stage in ("summarize", "doi", "deposition"):
        assert settings.thinking_for(stage) == settings.summary_thinking_budget
    for stage in ("extract", "dedup", "classify", "verify"):
        assert settings.thinking_for(stage) == settings.gemini_thinking_budget


# --- the ZDR hole this task exists to close ---------------------------------------------------------


def test_production_checks_zdr_when_openai_is_selected_by_llm_backend(monkeypatch):
    """THE regression this task exists for.

    While the ZDR guard keyed on summary_provider alone, LLM_BACKEND=openai with SUMMARY_PROVIDER at
    its default returned before the check and production started sending PHI to OpenAI with nobody
    having confirmed the organization's retention setting. A signed BAA does not cover that.
    """
    with pytest.raises(RuntimeError, match="OPENAI_ZDR_ACKNOWLEDGED"):
        _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="openai",
            OPENAI_API_KEY="sk-test",
            SUMMARY_BODY_MODEL="a",
            SUMMARY_TITLE_MODEL="b",
            AUDIT_MODEL="c",
        )


def test_production_checks_zdr_when_openai_is_reachable_only_by_an_override(monkeypatch):
    # Same hole, one level further down: a single stage is enough to send records to OpenAI.
    with pytest.raises(RuntimeError, match="OPENAI_ZDR_ACKNOWLEDGED"):
        _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="gemini",
            LLM_BACKEND_OVERRIDES="dedup=openai",
            OPENAI_API_KEY="sk-test",
            SUMMARY_BODY_MODEL="a",
            SUMMARY_TITLE_MODEL="b",
            AUDIT_MODEL="c",
        )


# --- the approved-destination check -----------------------------------------------------------------


def test_a_vllm_backend_without_a_model_refuses_to_start(monkeypatch):
    # A vLLM server serves exactly one model; an unset key would inherit a Gemini name and 404.
    with pytest.raises(RuntimeError, match="VLLM_MODEL"):
        _settings(monkeypatch, LLM_BACKEND="vllm", VLLM_BASE_URL="http://127.0.0.1:8000/v1")


def test_a_vllm_backend_without_a_base_url_refuses_to_start(monkeypatch):
    with pytest.raises(RuntimeError, match="VLLM_BASE_URL"):
        _settings(monkeypatch, LLM_BACKEND="vllm", VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8")


def test_production_refuses_an_unapproved_vllm_destination(monkeypatch):
    # Keyed on the DESTINATION, not the backend name: llm_backend=vllm only says which wire dialect
    # we speak, while the base URL decides where the record actually goes.
    with pytest.raises(RuntimeError, match="not approved to receive PHI"):
        _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="vllm",
            VLLM_BASE_URL="http://203.0.113.7:8000/v1",
            VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
        )


def test_the_refusal_names_the_destination_it_rejected(monkeypatch):
    # Whoever hits this at deploy time needs to see WHERE it was pointing, not just that it failed.
    with pytest.raises(RuntimeError, match=r"http://203\.0\.113\.7:8000"):
        _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="vllm",
            VLLM_BASE_URL="http://203.0.113.7:8000/v1",
            VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
        )


def test_production_accepts_an_approved_vllm_destination(monkeypatch):
    settings = _settings(monkeypatch, ENVIRONMENT="prod", LLM_BACKEND="vllm", **_VLLM)
    assert settings.backend_for("summarize") == "vllm"


def test_two_urls_differing_only_by_path_are_the_same_destination(monkeypatch):
    # "/v1" against "/v1/" is not a difference in where PHI goes, and refusing a production boot over
    # a trailing slash would be a self-inflicted outage.
    for url in ("http://127.0.0.1:8000", "http://127.0.0.1:8000/", "http://127.0.0.1:8000/v1/"):
        settings = _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="vllm",
            VLLM_BASE_URL=url,
            VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
        )
        assert settings.vllm_base_url == url


def test_production_ignores_an_env_supplied_allowlist(monkeypatch):
    # The allowlist lives in code so that widening it is visible in a diff and needs a deploy. In
    # prod the env value is IGNORED rather than rejected, so a stale dev value cannot brick a deploy.
    with pytest.raises(RuntimeError, match="not approved to receive PHI"):
        _settings(
            monkeypatch,
            ENVIRONMENT="prod",
            LLM_BACKEND="vllm",
            VLLM_BASE_URL="http://203.0.113.7:8000/v1",
            VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
            VLLM_APPROVED_ORIGINS="http://203.0.113.7:8000",
        )


def test_outside_production_the_env_allowlist_applies(monkeypatch):
    # So pointing a local stack at a scratch endpoint needs no code edit - which is what stops
    # someone commenting the guard out instead.
    settings = _settings(
        monkeypatch,
        ENVIRONMENT="dev",
        LLM_BACKEND="vllm",
        VLLM_BASE_URL="http://203.0.113.7:8000/v1",
        VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
        VLLM_APPROVED_ORIGINS="http://203.0.113.7:8000",
    )
    assert settings.backend_for("summarize") == "vllm"


def test_the_destination_check_is_production_only(monkeypatch):
    # Dev boxes point wherever they need to; they carry no PHI.
    settings = _settings(
        monkeypatch,
        ENVIRONMENT="dev",
        LLM_BACKEND="vllm",
        VLLM_BASE_URL="http://203.0.113.7:8000/v1",
        VLLM_MODEL="Qwen/Qwen3.6-35B-A3B-FP8",
    )
    assert settings.backend_for("summarize") == "vllm"


# --- which model the summarize stage resolves to, per backend ---------------------------------------
#
# Until 2026-09-15 the vllm path left the summarize triple EMPTY, under a note crediting the wiring
# to T5/T6 - but T5 grew the Protocol and T6 passed `stage`, so no task ever resolved a model here.
# The name that actually reached the pod was `summary_model`, which `_derive` defaults to a Gemini
# name for EVERY backend and which four call sites passed explicitly.


_SERVED = "Qwen/Qwen3.6-35B-A3B-FP8"


def test_a_vllm_summarize_stage_resolves_every_call_to_the_served_model(monkeypatch):
    # WHEN summarize resolves to vllm, THE SYSTEM SHALL answer model_for with VLLM_MODEL for all
    # three call types. A vLLM process serves exactly ONE model, so there is no tiering to express.
    settings = _settings(monkeypatch, LLM_BACKEND="vllm", **_VLLM)
    assert settings.model_for("body") == _SERVED
    assert settings.model_for("title") == _SERVED
    assert settings.model_for("audit") == _SERVED


def test_no_resolved_summarize_model_is_a_gemini_name_on_the_vllm_path(monkeypatch):
    # `summary_model` KEEPS its Gemini default here - it is set for every backend - so pinning it
    # alongside records WHY the call sites had to stop reading it.
    #
    # NON-EMPTY IS ASSERTED TOO, and that is not belt-and-braces. Written as a bare "not a gemini
    # name", this test PASSED with the resolver deleted: the triple then holds "", and
    # "".startswith("gemini") is False - so it was satisfied by the very broken state it exists to
    # catch. Found by mutating the resolver, never by reading it.
    settings = _settings(monkeypatch, LLM_BACKEND="vllm", **_VLLM)
    assert settings.summary_model.startswith("gemini"), "control: the Gemini default still exists"
    resolved = {kind: settings.model_for(kind) for kind in ("body", "title", "audit")}
    assert all(resolved.values()), f"a resolved summarize model is empty: {resolved}"
    assert not [kind for kind, name in resolved.items() if name.startswith("gemini")], resolved


def test_an_explicitly_configured_key_still_wins_on_the_vllm_path(monkeypatch):
    # Matching the Gemini branch: an operator setting the key has said something deliberate, and a
    # SECOND server can serve a second model even though one process cannot.
    settings = _settings(
        monkeypatch, LLM_BACKEND="vllm", SUMMARY_TITLE_MODEL="second-server/model", **_VLLM
    )
    assert settings.model_for("title") == "second-server/model"
    assert settings.model_for("body") == _SERVED


def test_a_stage_override_decides_the_summarize_models_not_the_global_backend(monkeypatch):
    # One per-stage override is enough to move summarize to the pod while the global setting still
    # reads gemini - the same fail-open shape resolved_backends() exists to close for the guards.
    settings = _settings(
        monkeypatch, LLM_BACKEND="gemini", LLM_BACKEND_OVERRIDES="summarize=vllm", **_VLLM
    )
    assert settings.model_for("body") == _SERVED


def test_summarize_held_on_gemini_keeps_the_gemini_tiering(monkeypatch):
    # The mirror, and the one that catches an OVER-broad fix: keying the resolver on
    # resolved_backends() rather than backend_for("summarize") would pass every test above and fail
    # this one, having pointed a Gemini-served stage at the pod's model.
    settings = _settings(
        monkeypatch, LLM_BACKEND="vllm", LLM_BACKEND_OVERRIDES="summarize=gemini", **_VLLM
    )
    assert settings.model_for("body") == "gemini-3.5-flash"
    assert settings.model_for("title") == "gemini-2.5-flash"
    assert settings.model_for("audit") == "gemini-2.5-flash"


# --- which PROVIDER a stage resolves to -------------------------------------------------------------
#
# THE MODEL WAS PER-STAGE AND THE TRANSPORT WAS NOT. `get_provider()` with no argument resolves
# through backend_for("summarize"), so a non-summarize caller pairing it with model_for_stage(stage)
# resolved its model through one stage and its transport through another. Shipped in #318 and found
# in review - the stage-independence test above could not see it, because it only ever inspects
# model_for_stage values and never asks which provider answers.
#
# Asserted on `.name`, never on object identity: get_provider is lru_cache'd per NAME, so two stages
# resolving to the same backend hand back the SAME object and an identity check would pass for the
# wrong reason.


def _provider_name(monkeypatch, stage, **env):
    from app.services.llm import provider_for_stage

    settings = _settings(monkeypatch, **env)
    monkeypatch.setattr("app.services.llm.get_settings", lambda: settings)
    return provider_for_stage(stage).name


def test_moving_one_stage_moves_its_provider_and_no_other(monkeypatch):
    # WHEN one stage is routed to vllm, THE SYSTEM SHALL answer provider_for_stage with vllm for
    # THAT stage and gemini for the rest. The model-side mirror of this already exists; without this
    # one, a caller can resolve the pod's model over the Gemini transport.
    env = {"LLM_BACKEND": "gemini", "LLM_BACKEND_OVERRIDES": "extract=vllm", **_VLLM}
    assert _provider_name(monkeypatch, "extract", **env) == "vllm"
    for untouched in ("dedup", "classify", "verify", "segment"):
        assert _provider_name(monkeypatch, untouched, **env) == "gemini", untouched


def test_a_stage_held_on_gemini_keeps_a_gemini_provider_while_the_rest_move(monkeypatch):
    # The mirror. A fix keyed on resolved_backends() rather than on THIS stage passes the test above
    # and fails here, having handed a Gemini-served stage the pod's transport.
    env = {"LLM_BACKEND": "vllm", "LLM_BACKEND_OVERRIDES": "classify=gemini", **_VLLM}
    assert _provider_name(monkeypatch, "classify", **env) == "gemini"
    assert _provider_name(monkeypatch, "dedup", **env) == "vllm"


def test_the_provider_and_the_model_resolve_through_the_SAME_stage(monkeypatch):
    # THE TEST THAT WOULD HAVE CAUGHT #318, stated as the invariant rather than as two facts. The
    # defect was not a wrong value on either side - each half was individually correct - it was the
    # two halves resolving through different stages. Both configurations below are ones where a bare
    # get_provider() disagrees with model_for_stage.
    from app.services.llm import provider_for_stage

    for overrides in ("extract=vllm", "summarize=vllm"):
        settings = _settings(
            monkeypatch, LLM_BACKEND="gemini", LLM_BACKEND_OVERRIDES=overrides, **_VLLM
        )
        monkeypatch.setattr("app.services.llm.get_settings", lambda: settings)
        provider = provider_for_stage("extract").name
        model_is_served = settings.model_for_stage("extract") == _SERVED
        assert (provider == "vllm") is model_is_served, (
            f"transport={provider} disagrees with the model under {overrides!r}"
        )


def test_summarize_is_refused_because_it_also_honours_summary_provider(monkeypatch):
    # Bare get_provider() honours summary_provider == "openai", which this resolver cannot see.
    # Routing summarize through here would drop a selector deployments set today, silently.
    from app.services.llm import provider_for_stage

    settings = _settings(monkeypatch, LLM_BACKEND="gemini")
    monkeypatch.setattr("app.services.llm.get_settings", lambda: settings)
    with pytest.raises(KeyError, match="summary_provider"):
        provider_for_stage("summarize")


def test_an_unknown_stage_is_refused_by_the_provider_resolver_too(monkeypatch):
    # Raised by backend_for, so there is no second stage list to drift out of step.
    from app.services.llm import provider_for_stage

    settings = _settings(monkeypatch, LLM_BACKEND="gemini")
    monkeypatch.setattr("app.services.llm.get_settings", lambda: settings)
    with pytest.raises(KeyError, match="unknown stage"):
        provider_for_stage("sumarize")


# --- which model a NON-summarize stage resolves to ---------------------------------------------------
#
# `model_for_stage` is a METHOD rather than a field rewrite, because `genai_model` is read by FOUR
# stages: segment, extract, doi and deposition. The test below that moves ONE of them and asserts the
# other three are unchanged is what would catch a later "simplification" into a field default.


def test_each_gemini_stage_keeps_the_setting_it_has_always_used(monkeypatch):
    # The regression guard: this must be inert on Gemini. Asserting against the NAMED settings pins
    # the mapping itself - that dedup and classify read classify_model while extract and segment read
    # genai_model - which is the part a refactor could quietly get wrong.
    s = _settings(monkeypatch, LLM_BACKEND="gemini")
    assert s.model_for_stage("segment") == s.genai_model
    assert s.model_for_stage("extract") == s.genai_model
    assert s.model_for_stage("doi") == s.genai_model
    assert s.model_for_stage("deposition") == s.genai_model
    assert s.model_for_stage("dedup") == s.classify_model
    assert s.model_for_stage("classify") == s.classify_model
    assert s.model_for_stage("verify") == s.verify_model
    # Control: the two tiers must actually differ, or every assertion above passes trivially.
    assert s.classify_model != s.genai_model


def test_every_non_summarize_stage_on_vllm_resolves_to_the_served_model(monkeypatch):
    # One process serves one model, so there is no per-stage tiering left to express.
    s = _settings(monkeypatch, LLM_BACKEND="vllm", **_VLLM)
    for stage in ("segment", "extract", "dedup", "classify", "verify", "doi", "deposition"):
        assert s.model_for_stage(stage) == _SERVED, stage


def test_moving_one_stage_to_the_pod_leaves_the_stages_sharing_its_setting_alone(monkeypatch):
    # THE test for this design. extract, segment, doi and deposition all read genai_model, so a
    # resolver implemented by REWRITING that field would move all four when only extract was asked
    # for - silently, and three of those do not cross the provider seam at all yet.
    s = _settings(monkeypatch, LLM_BACKEND="gemini", LLM_BACKEND_OVERRIDES="extract=vllm", **_VLLM)
    assert s.model_for_stage("extract") == _SERVED
    assert s.model_for_stage("segment") == s.genai_model
    assert s.model_for_stage("doi") == s.genai_model
    assert s.model_for_stage("deposition") == s.genai_model


def test_a_stage_held_on_gemini_keeps_its_own_model_while_the_rest_move(monkeypatch):
    # The mirror: an over-broad fix keyed on resolved_backends() rather than per stage passes the
    # test above and fails this one, having sent a Gemini-served stage the pod's model name.
    s = _settings(monkeypatch, LLM_BACKEND="vllm", LLM_BACKEND_OVERRIDES="classify=gemini", **_VLLM)
    assert s.model_for_stage("classify") == s.classify_model
    assert s.model_for_stage("dedup") == _SERVED


def test_summarize_is_refused_because_it_resolves_three_models(monkeypatch):
    # Returning one of body/title/audit from a per-stage resolver would invite a caller to use it for
    # all three and collapse the tiering with nothing surfacing it.
    s = _settings(monkeypatch, LLM_BACKEND="gemini")
    with pytest.raises(KeyError, match="three models"):
        s.model_for_stage("summarize")


def test_an_unknown_stage_is_refused_rather_than_answered(monkeypatch):
    # Mirrors backend_for and thinking_for: a typo must fail loudly, not resolve to something.
    s = _settings(monkeypatch, LLM_BACKEND="gemini")
    with pytest.raises(KeyError, match="unknown stage"):
        s.model_for_stage("sumarize")
