from __future__ import annotations

import pytest

from prometheus.analysis.analyzer import GeminiAnalyzer, MockAnalyzer, SCENE_SYSTEM_PROMPT
from prometheus.analysis.prompt_builder import build_scene_reconstruction_prompt
from prometheus.analysis.scene_schema import SCENE_CATEGORY_KEYS, SceneAnalysis
from prometheus.errors import PrometheusError
from prometheus.video.probe import VideoMetadata
from prometheus.video.sampler import SampledFrame
from prometheus.video.segmenter import Scene

from fakes import VALID_SCENE_JSON, FakeClient, FakeResponse


def _metadata() -> VideoMetadata:
    return VideoMetadata(
        path="video.mp4", container="mp4", duration=10.0, width=1280, height=720,
        fps=30.0, codec="h264", pixel_format="yuv420p", bitrate=1000, frame_count=300,
    )


def _scene() -> Scene:
    return Scene(index=0, start=2.0, end=5.5)


@pytest.fixture
def fake_frames(tmp_path) -> list[SampledFrame]:
    frames = []
    for i in range(2):
        path = tmp_path / f"frame_{i}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0fake")
        frames.append(SampledFrame(index=i, timestamp=3.0 + i, path=path))
    return frames


def test_gemini_scene_analysis_valid(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_SCENE_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analysis = analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    assert analysis.scene.start == 2.0
    assert analysis.scene.end == 5.5
    assert analysis.description == "A red cube rotating on a dark desk while a lamp swings."
    assert set(analysis.categories) == set(SCENE_CATEGORY_KEYS)
    assert analysis.categories["subjects"].confidence == 0.9
    assert analysis.analyzer["mode"] == "real"


def test_scene_model_receives_scene_prompt_and_frames(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_SCENE_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    kwargs = client.models.last_kwargs
    assert kwargs["config"].system_instruction == SCENE_SYSTEM_PROMPT
    assert len(kwargs["contents"]) == 1 + len(fake_frames)
    assert "scene 0" in kwargs["contents"][0].text.lower()
    assert "t=2.00s to t=5.50s" in kwargs["contents"][0].text


def test_scene_empty_analysis_rejected(fake_frames):
    empty = '{"description": "", "categories": {}}'
    client = FakeClient(lambda _: FakeResponse(empty))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="empty"):
        analyzer.analyze_scene(_metadata(), _scene(), fake_frames)


def test_scene_malformed_json_is_regenerated(fake_frames, monkeypatch):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(
        lambda call: FakeResponse("{oops") if call == 1 else FakeResponse(VALID_SCENE_JSON)
    )
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analysis = analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    assert analysis.description == "A red cube rotating on a dark desk while a lamp swings."
    assert client.models.calls == 2


def test_scene_request_includes_response_schema(fake_frames):
    from google.genai import types

    client = FakeClient(lambda _: FakeResponse(VALID_SCENE_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    config = client.models.last_kwargs["config"]
    assert config.response_mime_type == "application/json"
    schema = config.response_schema
    assert schema.type == types.Type.OBJECT
    assert set(schema.properties) == {"description", "categories"}
    assert schema.properties["description"].type == types.Type.STRING
    categories = schema.properties["categories"]
    assert categories.type == types.Type.OBJECT
    assert set(categories.properties) == set(SCENE_CATEGORY_KEYS)
    assert not schema.required
    finding = categories.properties["subjects"]
    assert finding.properties["observations"].type == types.Type.ARRAY
    assert finding.properties["confidence"].type == types.Type.NUMBER


def test_scene_description_only_parses(fake_frames):
    import json as json_module

    client = FakeClient(
        lambda _: FakeResponse(json_module.dumps({"description": "A red cube on a desk."}))
    )
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analysis = analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    assert analysis.description == "A red cube on a desk."
    analysis.validate()


def test_mock_scene_analysis(fake_frames):
    analysis = MockAnalyzer().analyze_scene(_metadata(), _scene(), fake_frames)
    assert set(analysis.categories) == set(SCENE_CATEGORY_KEYS)
    for finding in analysis.categories.values():
        assert finding.observations and finding.inferences
    analysis.validate()
    assert analysis.analyzer["mode"] == "mock"


def test_scene_reconstruction_prompt_content(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_SCENE_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analysis = analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    prompt = build_scene_reconstruction_prompt(analysis)
    assert "Scene 0 Reconstruction Prompt" in prompt
    assert "t=2.00s-5.50s" in prompt
    assert "A red cube rotating on a dark desk" in prompt
    assert "**Subjects:**" in prompt
    assert "**Camera Characteristics:**" in prompt
    assert "Recreate this scene" in prompt
    assert "INFERENCE:" not in prompt.split("## Structured breakdown")[0]


def test_scene_mock_warning_in_prompt(fake_frames):
    analysis = MockAnalyzer().analyze_scene(_metadata(), _scene(), fake_frames)
    prompt = build_scene_reconstruction_prompt(analysis)
    assert "MOCK MODE" in prompt


def test_scene_analysis_from_dict_round_trip(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_SCENE_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analysis = analyzer.analyze_scene(_metadata(), _scene(), fake_frames)
    rebuilt = SceneAnalysis.from_dict(analysis.to_dict(), analysis.scene)
    assert rebuilt.to_dict() == analysis.to_dict()
