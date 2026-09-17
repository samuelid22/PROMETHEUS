from __future__ import annotations

import pytest

from prometheus.config import PrometheusConfig
from prometheus.errors import PrometheusError


def test_defaults():
    config = PrometheusConfig.load()
    assert config.sampling.strategy == "uniform"
    assert config.sampling.frame_count == 8
    assert config.segmentation.scene_threshold == 0.3
    assert config.segmentation.min_scene_duration == 1.0
    assert config.segmentation.max_scenes == 12
    assert config.segmentation.frames_per_scene == 2
    assert config.analyzer.provider == "mock"


def test_load_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "sampling:\n  strategy: interval\n  frame_count: 5\n"
        "segmentation:\n  max_scenes: 6\n  frames_per_scene: 3\n"
        "analyzer:\n  provider: openai\n  model: gpt-4o-mini\noutput:\n  directory: results\n",
        encoding="utf-8",
    )
    config = PrometheusConfig.load(config_file)
    assert config.sampling.strategy == "interval"
    assert config.sampling.frame_count == 5
    assert config.segmentation.max_scenes == 6
    assert config.segmentation.frames_per_scene == 3
    assert config.analyzer.provider == "openai"
    assert config.analyzer.model == "gpt-4o-mini"
    assert str(config.output.directory) == "results"


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(PrometheusError, match="not found"):
        PrometheusConfig.load(tmp_path / "nope.yaml")


def test_invalid_strategy_rejected():
    config = PrometheusConfig.load()
    config.sampling.strategy = "bogus"
    with pytest.raises(PrometheusError, match="strategy"):
        config.validate()


def test_unknown_option_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("sampling:\n  frame_countdown: 5\n", encoding="utf-8")
    with pytest.raises(PrometheusError, match="Unknown config option"):
        PrometheusConfig.load(config_file)


def test_env_overrides_provider_and_model(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PROVIDER", "gemini")
    monkeypatch.setenv("PROMETHEUS_MODEL", "gemini-2.0-flash")
    config = PrometheusConfig.load()
    assert config.analyzer.provider == "gemini"
    assert config.analyzer.model == "gemini-2.0-flash"


def test_env_provider_with_default_model(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PROVIDER", "gemini")
    monkeypatch.delenv("PROMETHEUS_MODEL", raising=False)
    config = PrometheusConfig.load()
    assert config.analyzer.resolved_model() == "gemini-flash-lite-latest"


def test_invalid_env_provider_rejected(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PROVIDER", "nonexistent")
    with pytest.raises(PrometheusError, match="provider"):
        PrometheusConfig.load()


def test_invalid_segmentation_options_rejected():
    config = PrometheusConfig.load()
    config.segmentation.scene_threshold = 1.5
    with pytest.raises(PrometheusError, match="scene_threshold"):
        config.validate()
    config2 = PrometheusConfig.load()
    config2.segmentation.max_scenes = 0
    with pytest.raises(PrometheusError, match="max_scenes"):
        config2.validate()
    config3 = PrometheusConfig.load()
    config3.segmentation.frames_per_scene = 99
    with pytest.raises(PrometheusError, match="frames_per_scene"):
        config3.validate()


def test_cli_flags_beat_env(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_PROVIDER", "mock")
    import sys
    from main import _apply_overrides
    from types import SimpleNamespace
    config = PrometheusConfig.load()
    args = SimpleNamespace(analyzer="gemini", model=None, strategy=None, frames=None,
                           interval=None, scene_threshold=None, max_frames=None, output=None)
    config = _apply_overrides(config, args)
    assert config.analyzer.provider == "gemini"
