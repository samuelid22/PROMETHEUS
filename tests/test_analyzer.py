from __future__ import annotations

import json

from prometheus.analysis.analyzer import SYSTEM_PROMPT, MockAnalyzer, create_analyzer
from prometheus.analysis.prompt_builder import build_reconstructed_prompt
from prometheus.analysis.schema import CATEGORIES, AnalysisReport, CategoryFinding
from prometheus.config import AnalyzerConfig, SamplingConfig
from prometheus.video.probe import probe_video
from prometheus.video.sampler import FrameSampler


def _mock_report(synthetic_video, tmp_path):
    metadata = probe_video(synthetic_video)
    frames = FrameSampler(SamplingConfig(strategy="uniform", frame_count=4)).sample(metadata, tmp_path / "frames")
    return MockAnalyzer().analyze(metadata, frames)


def _real_report():
    data = {
        "summary": "A slow cinematic shot of a red cube.",
        "video_metadata": {"width": 1920, "height": 1080, "duration": 5.0, "fps": 24.0},
        "frames": [{"index": 0, "timestamp": 0.5}],
        "analyzer": {"provider": "gemini", "model": "gemini-3.6-flash", "mode": "real"},
        "categories": {
            "subject": {
                "observations": ["OBSERVATION: red matte cube centered at t=0.5s"],
                "inferences": ["INFERENCE: the cube is likely the intended hero subject"],
                "confidence": 0.9,
            },
            "camera_movement": {
                "observations": ["OBSERVATION: cube shifts left across frames"],
                "inferences": ["INFERENCE: apparent slow dolly-in, likely 24mm wide feel"],
                "confidence": 0.6,
            },
            "camera_angle": {
                "observations": [],
                "inferences": ["INFERENCE: appears to be a low-angle medium shot"],
                "confidence": 0.5,
            },
        },
    }
    return AnalysisReport.from_dict(data)


def test_mock_analyzer_populates_all_categories(synthetic_video, tmp_path):
    report = _mock_report(synthetic_video, tmp_path)
    assert set(report.categories) == {spec.key for spec in CATEGORIES}
    for finding in report.categories.values():
        assert finding.observations and finding.inferences
        assert 0.0 <= finding.confidence <= 1.0
    report.validate()


def test_mock_analyzer_marks_itself_as_mock(synthetic_video, tmp_path):
    report = _mock_report(synthetic_video, tmp_path)
    assert report.analyzer == {"provider": "mock", "model": "mock", "mode": "mock"}
    assert report.is_mock


def test_prompt_contains_sections(synthetic_video, tmp_path):
    report = _mock_report(synthetic_video, tmp_path)
    prompt = build_reconstructed_prompt(report)
    assert "Reconstructed Generation Prompt" in prompt
    assert "Generation prompt" in prompt
    assert "Structured breakdown" in prompt
    assert "Suggested technical parameters" in prompt
    assert "320x240" in prompt


def test_mock_warning_visible_in_prompt(synthetic_video, tmp_path):
    report = _mock_report(synthetic_video, tmp_path)
    prompt = build_reconstructed_prompt(report)
    assert "MOCK MODE" in prompt
    assert "no vision model was used" in prompt


def test_real_mode_prompt_shows_source():
    report = _real_report()
    prompt = build_reconstructed_prompt(report)
    assert "real vision model (gemini / gemini-3.6-flash)" in prompt
    assert "MOCK MODE" not in prompt


def test_global_prompt_renders_scene_timeline():
    report = _real_report()
    report.scenes = [
        {"index": 0, "start": 0.0, "end": 4.5, "duration": 4.5, "description": "Cube rotating."},
        {"index": 1, "start": 4.5, "end": 9.0, "duration": 4.5, "description": "Cube lifted away."},
    ]
    prompt = build_reconstructed_prompt(report)
    assert "## Scene timeline" in prompt
    assert "Scene 0: t=0.0s-4.5s (4.5s) — Cube rotating." in prompt
    assert "Scene 1: t=4.5s-9.0s (4.5s) — Cube lifted away." in prompt


def test_generation_prompt_is_structured_and_reproduction_oriented():
    report = _real_report()
    prompt = build_reconstructed_prompt(report)
    generation = prompt.split("## Generation prompt")[1].split("## Structured breakdown")[0]
    assert "Recreate a short video that reproduces the following visual characteristics" in generation
    assert "**Subject:**" in generation
    assert "**Camera:**" in generation
    assert "apparent slow dolly-in" in generation
    assert "INFERENCE" not in generation


def test_generation_prompt_skips_categories_without_inferences():
    report = _real_report()
    prompt = build_reconstructed_prompt(report)
    generation = prompt.split("## Generation prompt")[1].split("## Structured breakdown")[0]
    assert "**Lighting:**" not in generation


def test_generation_prompt_includes_depth_and_rhythm_sections():
    report = _real_report()
    report.categories["depth"] = CategoryFinding(
        observations=["Flat single-plane backdrop"],
        inferences=["Stage elements on one graphic plane with no parallax"],
        confidence=0.9,
    )
    report.categories["editing_rhythm"] = CategoryFinding(
        observations=["One continuous 6s shot"],
        inferences=["Single uninterrupted take; hold the framing steady throughout"],
        confidence=0.95,
    )
    prompt = build_reconstructed_prompt(report)
    generation = prompt.split("## Generation prompt")[1].split("## Structured breakdown")[0]
    assert "**Depth & space:**" in generation
    assert "**Editing rhythm:**" in generation
    assert "**Color palette & relationships:**" not in generation


def test_breakdown_shows_all_17_categories_with_findings():
    report = _real_report()
    prompt = build_reconstructed_prompt(report)
    assert "### Subject (confidence: 90%)" in prompt
    assert "### Camera Movement (confidence: 60%)" in prompt
    assert "### Apparent Camera Angle (confidence: 50%)" in prompt


def test_mock_placeholders_excluded_from_generation_prompt(synthetic_video, tmp_path):
    report = _mock_report(synthetic_video, tmp_path)
    prompt = build_reconstructed_prompt(report)
    generation = prompt.split("## Generation prompt")[1].split("## Structured breakdown")[0]
    assert "Placeholder inference" not in generation
    assert "No inferences available" in generation


def test_system_prompt_enforces_uncertainty_language():
    assert "never claim" in SYSTEM_PROMPT
    assert "known fact" in SYSTEM_PROMPT
    assert "apparent" in SYSTEM_PROMPT
    for concept in ("camera", "lens", "model", "seed", "prompt"):
        assert concept in SYSTEM_PROMPT


def test_system_prompt_bans_unsupported_claims_and_vagueness():
    assert "FORBIDDEN CLAIMS" in SYSTEM_PROMPT
    assert "seed value" in SYSTEM_PROMPT
    assert "reconstruction hypothesis" in SYSTEM_PROMPT
    assert "CONCRETENESS" in SYSTEM_PROMPT
    assert "Banned vague words" in SYSTEM_PROMPT
    for word in ("stunning", "breathtaking", "high-quality"):
        assert word in SYSTEM_PROMPT


def test_system_prompt_requires_signature_and_persistence():
    assert "SIGNATURE ELEMENTS" in SYSTEM_PROMPT
    assert "PERSISTENCE" in SYSTEM_PROMPT
    assert "EDITING RHYTHM" in SYSTEM_PROMPT
    assert "DEPTH" in SYSTEM_PROMPT


def test_schema_has_all_19_categories():
    assert len(CATEGORIES) == 19
    keys = {spec.key for spec in CATEGORIES}
    assert {
        "subject", "scene_environment", "composition", "camera_movement", "camera_angle",
        "lens_characteristics", "depth_of_field", "depth", "lighting", "color_palette",
        "textures_materials", "motion", "animation_characteristics", "graphic_design",
        "typography", "transitions", "editing_rhythm", "visual_effects", "overall_style",
    } == keys


def test_create_analyzer_factory():
    assert isinstance(create_analyzer(AnalyzerConfig(provider="mock")), MockAnalyzer)
