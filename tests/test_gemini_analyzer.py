from __future__ import annotations

import json

import pytest

from prometheus.analysis.analyzer import GeminiAnalyzer, create_analyzer
from prometheus.config import AnalyzerConfig
from prometheus.errors import PrometheusError
from prometheus.video.sampler import SampledFrame

from fakes import VALID_GLOBAL_JSON, FakeAPIError, FakeClient, FakeResponse

VALID_JSON = VALID_GLOBAL_JSON


@pytest.fixture
def fake_frames(tmp_path) -> list[SampledFrame]:
    frames = []
    for i in range(3):
        path = tmp_path / f"frame_{i}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0fake")
        frames.append(SampledFrame(index=i, timestamp=0.5 * (i + 1), path=path))
    return frames


def _metadata():
    from prometheus.video.probe import VideoMetadata

    return VideoMetadata(
        path="video.mp4", container="mp4", duration=3.0, width=640, height=360,
        fps=30.0, codec="h264", pixel_format="yuv420p", bitrate=1000, frame_count=90,
    )


def _no_api_keys(monkeypatch):
    for name in ("PROMETHEUS_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_missing_api_key_fails_loudly(monkeypatch, fake_frames):
    _no_api_keys(monkeypatch)
    with pytest.raises(PrometheusError, match="No API key found.*will not silently fall back"):
        GeminiAnalyzer(api_key=None)


def test_create_analyzer_does_not_fall_back_to_mock(monkeypatch):
    _no_api_keys(monkeypatch)
    config = AnalyzerConfig(provider="gemini")
    with pytest.raises(PrometheusError, match="No API key found"):
        create_analyzer(config)


def test_valid_response_produces_real_report(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_JSON))
    analyzer = GeminiAnalyzer(model="gemini-flash-lite-latest", api_key="test-key", client=client)
    report = analyzer.analyze(_metadata(), fake_frames)
    assert report.analyzer == {"provider": "gemini", "model": "gemini-flash-lite-latest", "mode": "real"}
    assert not report.is_mock
    assert report.summary == "A generated test scene."
    assert report.finding("subject").confidence == 0.9
    assert report.finding("scene_environment").observations == []
    report.validate()


def test_model_receives_frames_and_system_prompt(fake_frames):
    client = FakeClient(lambda _: FakeResponse(VALID_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analyzer.analyze(_metadata(), fake_frames)
    kwargs = client.models.last_kwargs
    assert kwargs["model"] == "gemini-flash-lite-latest"
    assert len(kwargs["contents"]) == 1 + len(fake_frames)
    assert kwargs["config"].system_instruction
    assert kwargs["config"].response_mime_type == "application/json"


def test_scene_context_passed_to_global_call(fake_frames):
    seen_prompts: list[str] = []

    def behavior(_):
        return FakeResponse(VALID_JSON)

    client = FakeClient(behavior)
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    scene_context = [
        {"index": 0, "start": 0.0, "end": 2.0, "duration": 2.0, "description": "A cube on a desk."},
    ]
    analyzer.analyze(_metadata(), fake_frames, scene_context=scene_context)
    contents = client.models.last_kwargs["contents"]
    seen_prompts.append(contents[0].text)
    assert any("Scene 0: 0.0s-2.0s" in text for text in seen_prompts)
    assert any("A cube on a desk." in text for text in seen_prompts)


def test_malformed_json_is_regenerated_before_failing(fake_frames, monkeypatch):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(
        lambda call: FakeResponse("{not json") if call == 1 else FakeResponse(VALID_JSON)
    )
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    report = analyzer.analyze(_metadata(), fake_frames)
    assert report.summary == "A generated test scene."
    assert client.models.calls == 2


def test_persistently_malformed_json_fails_after_regeneration_attempts(fake_frames, monkeypatch):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(lambda _: FakeResponse("{not json"))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="unusable analysis output after 4 attempts.*malformed JSON"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 4


def test_empty_response_is_regenerated_then_fails_with_finish_reason(fake_frames, monkeypatch):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(lambda _: FakeResponse(None, finish_reason="SAFETY"))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="unusable analysis output after 4 attempts.*no text content.*SAFETY"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 4


def test_all_empty_categories_are_regenerated_then_rejected(fake_frames, monkeypatch):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    empty = json.dumps({"summary": "s", "categories": {}})
    client = FakeClient(lambda _: FakeResponse(empty))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="unusable analysis output after 4 attempts.*every category was empty"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 4


def test_retries_on_rate_limit_then_succeeds(monkeypatch, fake_frames):
    sleeps: list[float] = []
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", sleeps.append)
    client = FakeClient(lambda call: FakeAPIError(429) if call <= 2 else FakeResponse(VALID_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    report = analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 3
    assert len(sleeps) == 2
    assert report.analyzer["mode"] == "real"


def test_non_retryable_error_fails_immediately(fake_frames):
    client = FakeClient(lambda _: FakeAPIError(401))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="API request failed.*401"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 1


def test_rate_limit_exhaustion_raises(monkeypatch, fake_frames):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(lambda _: FakeAPIError(429))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="after 6 attempts"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 6
