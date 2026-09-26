from __future__ import annotations

import json

import pytest

from prometheus.analysis.analyzer import (
    TOTAL_RETRY_DELAY_BUDGET_SECONDS,
    GeminiAnalyzer,
    create_analyzer,
)
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


def test_cumulative_budget_shared_across_sequential_calls(monkeypatch, fake_frames):
    import prometheus.analysis.analyzer as analyzer_module

    sleeps: list[float] = []
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", sleeps.append)
    monkeypatch.setattr(analyzer_module.random, "uniform", lambda a, b: 0.0)
    client = FakeClient(lambda _: FakeAPIError(503))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    for _ in range(3):
        with pytest.raises(PrometheusError, match="after 6 attempts"):
            analyzer.analyze(_metadata(), fake_frames)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    assert sum(sleeps) == TOTAL_RETRY_DELAY_BUDGET_SECONDS == 120.0
    assert analyzer._retry_delay_spent == 120.0
    assert client.models.calls == 6 + 6 + 1


def test_cumulative_budget_never_exceeds_limit_with_jitter(monkeypatch, fake_frames):
    import prometheus.analysis.analyzer as analyzer_module

    sleeps: list[float] = []
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", sleeps.append)
    monkeypatch.setattr(analyzer_module.random, "uniform", lambda a, b: 1.0)
    client = FakeClient(lambda _: FakeAPIError(503))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="after 6 attempts"):
        analyzer.analyze(_metadata(), fake_frames)
    with pytest.raises(PrometheusError, match="after 6 attempts"):
        analyzer.analyze(_metadata(), fake_frames)
    assert sleeps == [3.0, 5.0, 9.0, 17.0, 31.0, 3.0, 5.0, 9.0, 17.0]
    assert sum(sleeps) == 99.0 <= TOTAL_RETRY_DELAY_BUDGET_SECONDS
    assert analyzer._retry_delay_spent == 99.0
    assert client.models.calls == 6 + 5


def test_new_analyzer_starts_with_fresh_budget(monkeypatch, fake_frames):
    import prometheus.analysis.analyzer as analyzer_module

    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    monkeypatch.setattr(analyzer_module.random, "uniform", lambda a, b: 0.0)
    first = GeminiAnalyzer(api_key="test-key", client=FakeClient(lambda _: FakeAPIError(503)))
    with pytest.raises(PrometheusError, match="after 6 attempts"):
        first.analyze(_metadata(), fake_frames)
    assert first._retry_delay_spent == 60.0
    second = GeminiAnalyzer(api_key="test-key", client=FakeClient(lambda _: FakeAPIError(503)))
    assert second._retry_delay_spent == 0.0
    with pytest.raises(PrometheusError, match="after 6 attempts"):
        second.analyze(_metadata(), fake_frames)
    assert second._retry_delay_spent == 60.0


def test_output_regeneration_counts_toward_budget_and_unchanged(monkeypatch, fake_frames):
    import prometheus.analysis.analyzer as analyzer_module

    sleeps: list[float] = []
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", sleeps.append)
    monkeypatch.setattr(analyzer_module.random, "uniform", lambda a, b: 1.0)
    client = FakeClient(
        lambda call: FakeResponse("{not json") if call == 1 else FakeResponse(VALID_JSON)
    )
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    report = analyzer.analyze(_metadata(), fake_frames)
    assert report.summary == "A generated test scene."
    assert client.models.calls == 2
    assert sleeps == [2.0]
    assert analyzer._retry_delay_spent == 2.0


def test_global_request_includes_response_schema(fake_frames):
    from google.genai import types

    from prometheus.analysis.schema import CATEGORY_KEYS

    client = FakeClient(lambda _: FakeResponse(VALID_JSON))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    analyzer.analyze(_metadata(), fake_frames)
    config = client.models.last_kwargs["config"]
    assert config.response_mime_type == "application/json"
    schema = config.response_schema
    assert schema.type == types.Type.OBJECT
    assert set(schema.properties) == {"summary", "categories"}
    assert schema.properties["summary"].type == types.Type.STRING
    categories = schema.properties["categories"]
    assert categories.type == types.Type.OBJECT
    assert set(categories.properties) == set(CATEGORY_KEYS)
    assert schema.required == ["categories"]
    finding = categories.properties["subject"]
    assert finding.type == types.Type.OBJECT
    assert finding.properties["observations"].type == types.Type.ARRAY
    assert finding.properties["observations"].items.type == types.Type.STRING
    assert finding.properties["inferences"].type == types.Type.ARRAY
    assert finding.properties["inferences"].items.type == types.Type.STRING
    assert finding.properties["confidence"].type == types.Type.NUMBER
    assert not finding.required


def test_global_schema_subset_parses(fake_frames):
    payload = json.dumps({
        "summary": "A minimal valid scene.",
        "categories": {
            "subject": {
                "observations": ["OBSERVATION: red cube centered at t=0.5s"],
                "inferences": ["INFERENCE: the cube is likely the hero subject"],
                "confidence": 0.9,
            },
        },
    })
    client = FakeClient(lambda _: FakeResponse(payload))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    report = analyzer.analyze(_metadata(), fake_frames)
    assert report.summary == "A minimal valid scene."
    assert report.finding("subject").confidence == 0.9
    assert report.finding("lighting").observations == []
    assert report.finding("lighting").confidence == 0.0
    report.validate()


def test_global_missing_categories_rejected_after_retries(monkeypatch, fake_frames):
    monkeypatch.setattr("prometheus.analysis.analyzer.time.sleep", lambda _: None)
    client = FakeClient(lambda _: FakeResponse(json.dumps({"summary": "no categories"})))
    analyzer = GeminiAnalyzer(api_key="test-key", client=client)
    with pytest.raises(PrometheusError, match="unusable analysis output after 4 attempts"):
        analyzer.analyze(_metadata(), fake_frames)
    assert client.models.calls == 4
