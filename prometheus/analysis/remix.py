from __future__ import annotations

from prometheus.analysis.schema import AnalysisReport
from prometheus.errors import PrometheusError

REMIX_ATTRIBUTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("subject", "Subject", ("subject",)),
    ("environment", "Environment", ("scene_environment",)),
    ("style", "Style", ("overall_style",)),
    ("lighting", "Lighting", ("lighting",)),
    ("mood", "Mood", ("overall_style",)),
    ("color_palette", "Color palette", ("color_palette",)),
    ("camera", "Camera", ("camera_movement", "camera_angle")),
    ("motion", "Motion", ("motion", "animation_characteristics")),
)

_REMIX_ATTRIBUTE_KEYS = {attribute for attribute, _, _ in REMIX_ATTRIBUTES}


def apply_remix(
    report: AnalysisReport, overrides: dict[str, str]
) -> tuple[AnalysisReport, list[str]]:
    unknown = sorted(set(overrides) - _REMIX_ATTRIBUTE_KEYS)
    if unknown:
        raise PrometheusError(f"Unknown remix attributes: {', '.join(unknown)}")
    remixed = AnalysisReport.from_dict(report.to_dict())
    applied: list[str] = []
    for attribute, _, keys in REMIX_ATTRIBUTES:
        text = (overrides.get(attribute) or "").strip()
        if not text:
            continue
        for key in keys:
            finding = remixed.categories.get(key)
            if finding is None:
                continue
            if attribute == "mood":
                finding.inferences = [*finding.inferences, text]
            else:
                finding.inferences = [text]
            finding.confidence = 1.0
        applied.append(attribute)
    if not applied:
        raise PrometheusError("No remix attributes provided")
    return remixed, applied
