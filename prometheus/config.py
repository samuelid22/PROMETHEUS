from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from prometheus.errors import PrometheusError

SAMPLING_STRATEGIES = ("uniform", "interval")
ANALYZER_PROVIDERS = ("mock", "openai", "gemini")
DEFAULT_MODELS = {"openai": "gpt-4o", "gemini": "gemini-flash-lite-latest"}
IMAGE_FORMATS = ("jpg", "png")

ENV_PROVIDER = "PROMETHEUS_PROVIDER"
ENV_MODEL = "PROMETHEUS_MODEL"


@dataclass
class SamplingConfig:
    strategy: str = "uniform"
    frame_count: int = 8
    interval_seconds: float = 1.0
    max_frames: int = 16
    image_format: str = "jpg"
    image_quality: int = 2


@dataclass
class SegmentationConfig:
    scene_threshold: float = 0.3
    min_scene_duration: float = 1.0
    max_scenes: int = 12
    frames_per_scene: int = 2


@dataclass
class AnalyzerConfig:
    provider: str = "mock"
    model: str | None = None

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return DEFAULT_MODELS.get(self.provider, "mock")


@dataclass
class OutputConfig:
    directory: Path = Path("output")


@dataclass
class PrometheusConfig:
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "PrometheusConfig":
        data: dict[str, Any] = {}
        if path is not None:
            config_path = Path(path)
            if not config_path.is_file():
                raise PrometheusError(f"Config file not found: {config_path}")
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                raise PrometheusError("Config file must contain a YAML mapping")
            data = loaded or {}
        config = cls()
        config._merge(data)
        config._apply_env_overrides()
        config.validate()
        return config

    def _apply_env_overrides(self) -> None:
        provider = os.environ.get(ENV_PROVIDER)
        if provider:
            self.analyzer.provider = provider
        model = os.environ.get(ENV_MODEL)
        if model:
            self.analyzer.model = model

    def _merge(self, data: dict[str, Any]) -> None:
        sections = {
            "sampling": self.sampling,
            "segmentation": self.segmentation,
            "analyzer": self.analyzer,
        }
        for key, value in data.items():
            if key == "output":
                directory = value.get("directory") if isinstance(value, dict) else value
                if not directory:
                    raise PrometheusError("output.directory must not be empty")
                self.output.directory = Path(directory)
            elif key in sections:
                if not isinstance(value, dict):
                    raise PrometheusError(f"Config section '{key}' must be a mapping")
                target = sections[key]
                for option, option_value in value.items():
                    if not hasattr(target, option):
                        raise PrometheusError(f"Unknown config option: {key}.{option}")
                    setattr(target, option, option_value)
            else:
                raise PrometheusError(f"Unknown config section: {key}")

    def validate(self) -> None:
        s = self.sampling
        if s.strategy not in SAMPLING_STRATEGIES:
            raise PrometheusError(f"strategy must be one of {SAMPLING_STRATEGIES}, got '{s.strategy}'")
        if not 1 <= s.frame_count <= 64:
            raise PrometheusError(f"frame_count must be between 1 and 64, got {s.frame_count}")
        if s.interval_seconds <= 0:
            raise PrometheusError(f"interval_seconds must be positive, got {s.interval_seconds}")
        if not 1 <= s.max_frames <= 64:
            raise PrometheusError(f"max_frames must be between 1 and 64, got {s.max_frames}")
        if s.image_format not in IMAGE_FORMATS:
            raise PrometheusError(f"image_format must be one of {IMAGE_FORMATS}, got '{s.image_format}'")
        if not 1 <= s.image_quality <= 31:
            raise PrometheusError(f"image_quality must be between 1 and 31, got {s.image_quality}")
        g = self.segmentation
        if not 0.0 < g.scene_threshold < 1.0:
            raise PrometheusError(f"scene_threshold must be between 0 and 1, got {g.scene_threshold}")
        if g.min_scene_duration <= 0:
            raise PrometheusError(f"min_scene_duration must be positive, got {g.min_scene_duration}")
        if not 1 <= g.max_scenes <= 32:
            raise PrometheusError(f"max_scenes must be between 1 and 32, got {g.max_scenes}")
        if not 1 <= g.frames_per_scene <= 8:
            raise PrometheusError(f"frames_per_scene must be between 1 and 8, got {g.frames_per_scene}")
        if self.analyzer.provider not in ANALYZER_PROVIDERS:
            raise PrometheusError(f"provider must be one of {ANALYZER_PROVIDERS}, got '{self.analyzer.provider}'")
        if self.analyzer.model is not None and not str(self.analyzer.model).strip():
            raise PrometheusError("model must not be blank when set")
