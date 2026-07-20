"""Seam-level cross-cutting logic in genai_retry: the thinking-default applied to every call.

Pure-Python (no Vertex, no DB) - they exercise config mutation only. The retry loop itself and
the Redis limiter are covered elsewhere; here we prove the thinking default is applied centrally
and that an explicit per-call thinking_config always wins.
"""

from google.genai import types

from app.config import get_settings
from app.services.genai_retry import _apply_thinking_default


def test_thinking_default_applied_when_config_has_none():
    config = types.GenerateContentConfig(temperature=0.0)
    assert config.thinking_config is None
    _apply_thinking_default(config)
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == get_settings().gemini_thinking_budget


def test_explicit_thinking_config_is_preserved():
    # A call that opts into thinking (e.g. a validated-to-need-it task) must not be overridden.
    config = types.GenerateContentConfig(
        temperature=0.0, thinking_config=types.ThinkingConfig(thinking_budget=512)
    )
    _apply_thinking_default(config)
    assert config.thinking_config.thinking_budget == 512


def test_mapping_config_gets_thinking_default():
    config: dict = {"temperature": 0.0}
    _apply_thinking_default(config)
    assert isinstance(config["thinking_config"], types.ThinkingConfig)
    assert config["thinking_config"].thinking_budget == get_settings().gemini_thinking_budget


def test_mapping_config_with_thinking_is_preserved():
    sentinel = types.ThinkingConfig(thinking_budget=256)
    config = {"temperature": 0.0, "thinking_config": sentinel}
    _apply_thinking_default(config)
    assert config["thinking_config"] is sentinel


def test_none_config_is_a_noop():
    _apply_thinking_default(None)  # must not raise
