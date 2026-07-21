"""Segmentation keeps thinking (carve-out) even though the global seam default disables it.

An A/B on labeled cases showed thinking-off regresses segmentation strict doc-F1, so the
segmentation window call sets its own thinking_config. This guards that (a) the segmentation config
carries segment_thinking_budget and (b) the seam's global default does NOT override it.
"""

from app.config import get_settings
from app.services.genai_retry import _apply_thinking_default
from app.services.segment_engine import _generation_config


def test_segmentation_config_keeps_its_thinking_budget():
    config = _generation_config()
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == get_settings().segment_thinking_budget


def test_seam_does_not_override_segmentation_thinking():
    # Even with the global gemini_thinking_budget (default 0), segmentation's explicit budget wins.
    config = _generation_config()
    budget = config.thinking_config.thinking_budget
    _apply_thinking_default(config)
    assert config.thinking_config.thinking_budget == budget


def test_segmentation_default_is_dynamic_thinking():
    # Default -1 = model-dynamic thinking, i.e. pre-change behavior preserved.
    assert get_settings().segment_thinking_budget == -1
