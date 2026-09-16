"""Segmentation keeps thinking (carve-out) even though the global seam default disables it.

An A/B on labeled cases showed thinking-off regresses segmentation strict doc-F1 by over-segmenting,
so segmentation must keep a dynamic budget where every other structured call runs at 0.

WHERE THE GUARANTEE LIVES MOVED when segmentation was routed through the provider seam. It used to
be an explicit `thinking_config` built by `segment_engine._generation_config`; that function is gone,
and the budget is now resolved by `Settings.thinking_for(stage)` inside the Gemini provider from the
stage string the service passes. So there are two halves to guard, and they are guarded in two
places:

* THIS FILE: that the `segment` stage maps to the carve-out, and that the carve-out is actually
  different from what everything else gets.
* `test_segment_engine_request.py`: that the SERVICE passes `stage="segment"`. That half matters
  because a wrong stage string silently resolves to `gemini_thinking_budget` of 0, and segmentation
  has no fail-safe - it would simply segment worse, with nothing raised and nothing logged.
"""

from app.config import get_settings


def test_the_segment_stage_resolves_to_the_segmentation_carve_out():
    """WHEN the segment stage is resolved, THE SYSTEM SHALL return segment_thinking_budget."""
    settings = get_settings()
    assert settings.thinking_for("segment") == settings.segment_thinking_budget


def test_the_carve_out_differs_from_what_every_other_structured_call_gets():
    """A carve-out only means something while it differs from the default it is carved out of.

    Without this, `thinking_for` could return the global budget for every stage and the test above
    would still pass - proving the plumbing works and the policy is gone.
    """
    settings = get_settings()
    assert settings.thinking_for("segment") != settings.gemini_thinking_budget
    assert settings.thinking_for("classify") == settings.gemini_thinking_budget
    assert settings.thinking_for("extract") == settings.gemini_thinking_budget


def test_segmentation_default_is_dynamic_thinking():
    # Default -1 = model-dynamic thinking, i.e. pre-change behavior preserved.
    assert get_settings().segment_thinking_budget == -1
