from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from prometheus.analysis.schema import CategoryFinding
from prometheus.errors import PrometheusError
from prometheus.video.segmenter import Scene


@dataclass(frozen=True)
class SceneCategorySpec:
    key: str
    label: str
    description: str


SCENE_CATEGORIES: tuple[SceneCategorySpec, ...] = (
    SceneCategorySpec("subjects", "Subjects", "People, creatures, characters and key objects in the scene"),
    SceneCategorySpec("environment", "Environment", "Location, setting, background and world details"),
    SceneCategorySpec("composition", "Composition", "Framing, layout, focal points, negative space"),
    SceneCategorySpec("camera", "Camera Characteristics", "Apparent shot type, angle, movement and lens feel"),
    SceneCategorySpec("motion", "Motion", "Subject and camera motion within the scene"),
    SceneCategorySpec("lighting", "Lighting", "Light sources, direction, quality and mood"),
    SceneCategorySpec("style", "Style", "Rendering style, medium, aesthetic treatment"),
    SceneCategorySpec("graphics_text", "Graphics & Text", "On-screen text, titles, UI overlays, graphic elements"),
)

SCENE_CATEGORY_KEYS = tuple(spec.key for spec in SCENE_CATEGORIES)


@dataclass
class SceneAnalysis:
    scene: Scene
    description: str = ""
    analyzer: dict[str, str] = field(default_factory=dict)
    categories: dict[str, CategoryFinding] = field(default_factory=lambda: _empty_scene_categories())

    def __post_init__(self) -> None:
        for key in SCENE_CATEGORY_KEYS:
            if key not in self.categories:
                self.categories[key] = CategoryFinding()

    def validate(self) -> None:
        has_content = self.description.strip() != "" or any(
            f.observations or f.inferences for f in self.categories.values()
        )
        if not has_content:
            raise PrometheusError(
                f"Scene {self.scene.index} analysis is empty (no description and no findings)"
            )
        for key in SCENE_CATEGORY_KEYS:
            finding = self.categories.get(key)
            if finding is None:
                raise PrometheusError(f"Missing scene analysis category: '{key}'")
            if not 0.0 <= finding.confidence <= 1.0:
                raise PrometheusError(
                    f"Confidence for scene {self.scene.index} '{key}' must be within [0, 1], got {finding.confidence}"
                )
            for text in finding.observations + finding.inferences:
                if not isinstance(text, str) or not text.strip():
                    raise PrometheusError(f"Empty entry in scene {self.scene.index} '{key}'")

    def to_dict(self) -> dict:
        return {
            "scene": self.scene.to_dict(),
            "description": self.description,
            "analyzer": self.analyzer,
            "categories": {key: self.categories[key].to_dict() for key in SCENE_CATEGORY_KEYS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], scene: Scene) -> "SceneAnalysis":
        if not isinstance(data, dict):
            raise PrometheusError("Scene analysis data must be a mapping")
        description = str(data.get("description", ""))
        categories_data = data.get("categories", {})
        if not isinstance(categories_data, dict):
            raise PrometheusError("'categories' must be a mapping")
        categories: dict[str, CategoryFinding] = {}
        for key in SCENE_CATEGORY_KEYS:
            raw = categories_data.get(key, {})
            if not isinstance(raw, dict):
                raise PrometheusError(f"Scene category '{key}' must be a mapping")
            observations = _string_list(raw.get("observations", []))
            inferences = _string_list(raw.get("inferences", []))
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
            except (TypeError, ValueError):
                raise PrometheusError(f"Invalid confidence value for scene category '{key}'") from None
            categories[key] = CategoryFinding(
                observations=observations, inferences=inferences, confidence=confidence
            )
        raw_analyzer = data.get("analyzer", {})
        analyzer = (
            {str(k): str(v) for k, v in raw_analyzer.items()} if isinstance(raw_analyzer, dict) else {}
        )
        return cls(scene=scene, description=description, analyzer=analyzer, categories=categories)


def _empty_scene_categories() -> dict[str, CategoryFinding]:
    return {key: CategoryFinding() for key in SCENE_CATEGORY_KEYS}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise PrometheusError("Scene category entries must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]
