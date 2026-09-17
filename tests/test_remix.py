from __future__ import annotations

import pytest

from prometheus.analysis.prompt_builder import build_reconstructed_prompt
from prometheus.analysis.remix import REMIX_ATTRIBUTES, apply_remix
from prometheus.analysis.schema import AnalysisReport
from prometheus.errors import PrometheusError


def _report() -> AnalysisReport:
    return AnalysisReport.from_dict({
        "summary": "A test video.",
        "video_metadata": {"width": 640, "height": 360, "duration": 6.0, "fps": 30.0},
        "frames": [{"index": 0, "timestamp": 0.5}],
        "analyzer": {"provider": "gemini", "model": "test", "mode": "real"},
        "categories": {
            "subject": {
                "observations": ["A red cube at t=0.5s"],
                "inferences": ["The cube is likely the hero subject"],
                "confidence": 0.9,
            },
            "camera_movement": {
                "observations": ["Framing shifts left between frames"],
                "inferences": ["apparent slow pan left"],
                "confidence": 0.7,
            },
            "camera_angle": {
                "observations": ["Low horizon line"],
                "inferences": ["low-angle medium shot"],
                "confidence": 0.6,
            },
            "lens_characteristics": {
                "observations": ["Straight verticals"],
                "inferences": ["wide 24mm feel"],
                "confidence": 0.5,
            },
            "motion": {
                "observations": ["Cube edges move between frames"],
                "inferences": ["cube rotates slowly clockwise"],
                "confidence": 0.7,
            },
            "animation_characteristics": {
                "observations": ["Constant speed between frames"],
                "inferences": ["linear keyframe easing"],
                "confidence": 0.6,
            },
            "lighting": {
                "observations": ["Hard shadow"],
                "inferences": ["Single key light from the right"],
                "confidence": 0.7,
            },
            "overall_style": {
                "observations": ["Flat digital look"],
                "inferences": ["Retro test-pattern style"],
                "confidence": 0.8,
            },
        },
    })


def test_remix_replaces_mapped_category_inferences():
    report, applied = apply_remix(_report(), {"subject": "a chrome dragon"})
    assert applied == ["subject"]
    assert report.finding("subject").inferences == ["a chrome dragon"]
    assert report.finding("subject").confidence == 1.0
    assert report.finding("subject").observations == ["A red cube at t=0.5s"]


def test_remix_does_not_touch_other_categories():
    report, _ = apply_remix(_report(), {"lighting": "neon nightclub"})
    assert report.finding("subject").inferences == ["The cube is likely the hero subject"]
    assert report.finding("overall_style").inferences == ["Retro test-pattern style"]


def test_mood_appends_to_style():
    report, applied = apply_remix(_report(), {"mood": "tense and ominous"})
    assert applied == ["mood"]
    assert report.finding("overall_style").inferences == [
        "Retro test-pattern style",
        "tense and ominous",
    ]


def test_style_then_mood_order():
    report, applied = apply_remix(_report(), {"style": "noir", "mood": "tense"})
    assert applied == ["style", "mood"]
    assert report.finding("overall_style").inferences == ["noir", "tense"]


def test_blank_overrides_ignored():
    report, applied = apply_remix(_report(), {"subject": "  ", "lighting": "neon"})
    assert applied == ["lighting"]


def test_unknown_attribute_rejected():
    with pytest.raises(PrometheusError, match="Unknown remix attributes"):
        apply_remix(_report(), {"soundtrack": "synthwave"})


def test_empty_overrides_rejected():
    with pytest.raises(PrometheusError, match="No remix attributes"):
        apply_remix(_report(), {})


def test_remix_flows_through_existing_prompt_builder():
    report, _ = apply_remix(_report(), {"subject": "a chrome dragon", "lighting": "neon nightclub"})
    prompt = build_reconstructed_prompt(report)
    assert "a chrome dragon" in prompt
    assert "neon nightclub" in prompt
    assert "The cube is likely the hero subject" not in prompt


def test_original_report_not_mutated():
    original = _report()
    apply_remix(original, {"subject": "a chrome dragon"})
    assert original.finding("subject").inferences == ["The cube is likely the hero subject"]


def test_remix_attributes_cover_spec():
    attributes = {attribute for attribute, _, _ in REMIX_ATTRIBUTES}
    assert attributes == {
        "subject", "environment", "style", "lighting", "mood", "color_palette",
        "camera", "motion",
    }


def test_camera_remix_replaces_movement_and_angle_only():
    report, applied = apply_remix(_report(), {"camera": "slow orbital dolly around the cube"})
    assert applied == ["camera"]
    assert report.finding("camera_movement").inferences == ["slow orbital dolly around the cube"]
    assert report.finding("camera_angle").inferences == ["slow orbital dolly around the cube"]
    assert report.finding("lens_characteristics").inferences == ["wide 24mm feel"]


def test_camera_remix_preserves_observations():
    report, _ = apply_remix(_report(), {"camera": "handheld documentary tracking"})
    assert report.finding("camera_movement").observations == ["Framing shifts left between frames"]


def test_motion_remix_replaces_motion_and_animation():
    report, applied = apply_remix(_report(), {"motion": "heavy slow motion with drifting dust"})
    assert applied == ["motion"]
    assert report.finding("motion").inferences == ["heavy slow motion with drifting dust"]
    assert report.finding("animation_characteristics").inferences == ["heavy slow motion with drifting dust"]


def test_camera_remix_flows_through_prompt():
    report, _ = apply_remix(_report(), {"camera": "slow orbital dolly around the cube"})
    prompt = build_reconstructed_prompt(report)
    assert "slow orbital dolly around the cube" in prompt
    assert "apparent slow pan left" not in prompt
    assert "wide 24mm feel" in prompt
