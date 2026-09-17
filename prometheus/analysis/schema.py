from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from prometheus.errors import PrometheusError


@dataclass(frozen=True)
class CategorySpec:
    key: str
    label: str
    description: str


CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec("subject", "Subject", "Main subject(s), people, creatures, characters and key objects; identity, placement, scale and staging within the frame"),
    CategorySpec("scene_environment", "Scene / Environment", "Location, setting, background and world details"),
    CategorySpec("composition", "Composition", "Framing, layout, subject placement, focal points, negative space, symmetry"),
    CategorySpec("camera_movement", "Camera Movement", "Apparent camera motion: pans, dollies, zooms, orbit, handheld feel"),
    CategorySpec("camera_angle", "Apparent Camera Angle", "Shot type, angle and height: wide, close-up, low/high angle, POV"),
    CategorySpec("lens_characteristics", "Apparent Lens Characteristics", "Focal length feel, distortion, bokeh, focus breathing, anamorphic hints"),
    CategorySpec("depth_of_field", "Depth of Field", "Focus plane, background blur, rack-focus behavior"),
    CategorySpec("depth", "Depth & Spatial Layers", "Foreground, midground and background organization; layering, parallax cues; flat graphic versus deep photographic staging"),
    CategorySpec("lighting", "Lighting", "Light sources, direction, quality and mood of illumination"),
    CategorySpec("color_palette", "Color Palette & Relationships", "Dominant colors, how colors relate and interact, color grading, saturation, contrast"),
    CategorySpec("textures_materials", "Textures & Materials", "Surface qualities and textures of subjects and environment"),
    CategorySpec("motion", "Motion", "Subject movement and overall motion across the video"),
    CategorySpec("animation_characteristics", "Animation Characteristics", "Keyframe smoothness, easing, frame-rate feel, morphing, interpolation artifacts"),
    CategorySpec("graphic_design", "Graphic Design", "Layout systems, UI overlays, shapes, iconography, branding elements"),
    CategorySpec("typography", "Typography", "On-screen text, titles, fonts and lettering"),
    CategorySpec("transitions", "Transitions", "Cuts, fades, morphs and transitions between shots"),
    CategorySpec("editing_rhythm", "Editing Rhythm & Pacing", "Shot-length range, cut frequency and pacing arc across the video; use the scene timeline durations"),
    CategorySpec("visual_effects", "Visual Effects", "Particles, simulations, glow, glitch, compositing artifacts"),
    CategorySpec("overall_style", "Overall Visual Style", "Genre, medium, rendering style, era, aesthetic references"),
)

CATEGORY_KEYS = tuple(spec.key for spec in CATEGORIES)


@dataclass
class CategoryFinding:
    observations: list[str] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisReport:
    video_metadata: dict = field(default_factory=dict)
    frames: list[dict] = field(default_factory=list)
    summary: str = ""
    analyzer: dict[str, str] = field(default_factory=dict)
    scenes: list[dict] = field(default_factory=list)
    categories: dict[str, CategoryFinding] = field(default_factory=lambda: _empty_categories())

    @property
    def is_mock(self) -> bool:
        return self.analyzer.get("mode") == "mock"

    def __post_init__(self) -> None:
        for key in CATEGORY_KEYS:
            if key not in self.categories:
                self.categories[key] = CategoryFinding()

    def finding(self, key: str) -> CategoryFinding:
        return self.categories[key]

    def validate(self) -> None:
        for key in CATEGORY_KEYS:
            finding = self.categories.get(key)
            if not isinstance(finding, CategoryFinding):
                raise PrometheusError(f"Missing or invalid analysis category: '{key}'")
            if not 0.0 <= finding.confidence <= 1.0:
                raise PrometheusError(f"Confidence for '{key}' must be within [0, 1], got {finding.confidence}")
            for text in finding.observations + finding.inferences:
                if not isinstance(text, str) or not text.strip():
                    raise PrometheusError(f"Empty entry in '{key}'")

    def to_dict(self) -> dict:
        return {
            "video_metadata": self.video_metadata,
            "frames": self.frames,
            "summary": self.summary,
            "analyzer": self.analyzer,
            "scenes": self.scenes,
            "categories": {key: self.categories[key].to_dict() for key in CATEGORY_KEYS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisReport":
        if not isinstance(data, dict):
            raise PrometheusError("Analysis data must be a mapping")
        categories_data = data.get("categories", {})
        if not isinstance(categories_data, dict):
            raise PrometheusError("'categories' must be a mapping")
        categories: dict[str, CategoryFinding] = {}
        for key in CATEGORY_KEYS:
            raw = categories_data.get(key, {})
            if not isinstance(raw, dict):
                raise PrometheusError(f"Category '{key}' must be a mapping")
            observations = _string_list(raw.get("observations", []), f"{key}.observations")
            inferences = _string_list(raw.get("inferences", []), f"{key}.inferences")
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
            except (TypeError, ValueError):
                raise PrometheusError(f"Invalid confidence value for '{key}'") from None
            categories[key] = CategoryFinding(observations=observations, inferences=inferences, confidence=confidence)
        return cls(
            video_metadata=data.get("video_metadata", {}),
            frames=data.get("frames", []),
            summary=str(data.get("summary", "")),
            analyzer=_provenance(data.get("analyzer")),
            scenes=[s for s in data.get("scenes", []) if isinstance(s, dict)],
            categories=categories,
        )


def _provenance(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _empty_categories() -> dict[str, CategoryFinding]:
    return {key: CategoryFinding() for key in CATEGORY_KEYS}


def _string_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise PrometheusError(f"{label} must be a list of strings")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned
