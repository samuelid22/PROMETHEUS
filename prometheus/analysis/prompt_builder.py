from __future__ import annotations

import math
import re

from prometheus.analysis.scene_schema import SCENE_CATEGORIES, SceneAnalysis
from prometheus.analysis.schema import AnalysisReport, CATEGORIES

_OBS_INFERENCE_PREFIX_RE = re.compile(r"^\s*(OBSERVATION|INFERENCE)\s*:\s*", re.IGNORECASE)

_GENERATION_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Subject", ("subject",)),
    ("Scene", ("scene_environment",)),
    ("Composition", ("composition",)),
    ("Camera", ("camera_movement", "camera_angle")),
    ("Lens & focus", ("lens_characteristics", "depth_of_field")),
    ("Depth & space", ("depth",)),
    ("Lighting", ("lighting",)),
    ("Color palette & relationships", ("color_palette",)),
    ("Textures & materials", ("textures_materials",)),
    ("Motion & animation", ("motion", "animation_characteristics")),
    ("Editing rhythm", ("editing_rhythm",)),
    ("Graphics & typography", ("graphic_design", "typography")),
    ("Effects & transitions", ("visual_effects", "transitions")),
    ("Overall style", ("overall_style",)),
)

_SCENE_GENERATION_SECTIONS: tuple[tuple[str, str], ...] = tuple(
    (spec.label, spec.key) for spec in SCENE_CATEGORIES
)


def build_reconstructed_prompt(report: AnalysisReport) -> str:
    lines: list[str] = []
    lines.append("# Reconstructed Generation Prompt")
    lines.append("")
    summary = report.summary.strip() or "Creative recipe inferred from sampled frames of an AI-generated video."
    lines.append(f"> {summary}")
    lines.append("")
    source = _provenance_line(report)
    if source:
        lines.append(f"> {source}")
        lines.append("")
    lines.extend(_generation_prompt(report))
    lines.append("")
    timeline = _scene_timeline(report)
    if timeline:
        lines.extend(timeline)
        lines.append("")
    breakdown = _structured_breakdown(report)
    if breakdown:
        lines.append("## Structured breakdown")
        lines.append("")
        lines.extend(breakdown)
    params = _technical_parameters(report)
    if params:
        lines.append("")
        lines.append("## Suggested technical parameters")
        lines.append("")
        lines.extend(params)
    return "\n".join(lines).strip() + "\n"


def _generation_prompt(report: AnalysisReport) -> list[str]:
    lines = [
        "## Generation prompt",
        "",
        "Recreate a short video that reproduces the following visual characteristics:",
        "",
    ]
    found_any = False
    for label, keys in _GENERATION_SECTIONS:
        text = _join_inferences(report, keys)
        if text:
            found_any = True
            lines.append(f"**{label}:** {text}")
    if not found_any:
        return [
            "## Generation prompt",
            "",
            "No inferences available; connect a multimodal analyzer to generate a prompt.",
        ]
    return lines


def _join_inferences(report: AnalysisReport, keys: tuple[str, ...]) -> str:
    sentences: list[str] = []
    for key in keys:
        finding = report.categories.get(key)
        if finding is None:
            continue
        for inference in finding.inferences:
            text = _strip_tags(inference.strip()).rstrip(".")
            if text and not text.lower().startswith("placeholder"):
                sentences.append(f"{text}.")
    return " ".join(sentences)


def _strip_tags(text: str) -> str:
    return _OBS_INFERENCE_PREFIX_RE.sub("", text)


def _scene_timeline(report: AnalysisReport) -> list[str]:
    if not report.scenes:
        return []
    lines = ["## Scene timeline", ""]
    for scene in report.scenes:
        index = scene.get("index", "?")
        start = scene.get("start", 0.0)
        end = scene.get("end", 0.0)
        duration = scene.get("duration", end - start)
        description = scene.get("description", "")
        lines.append(f"- Scene {index}: t={start:.1f}s-{end:.1f}s ({duration:.1f}s) — {description}")
    return lines


def build_scene_reconstruction_prompt(analysis: SceneAnalysis) -> str:
    scene = analysis.scene
    lines: list[str] = []
    lines.append(f"# Scene {scene.index} Reconstruction Prompt")
    lines.append("")
    lines.append(f"> Scene {scene.index}: t={scene.start:.2f}s-{scene.end:.2f}s "
                 f"(duration {scene.duration:.2f}s)")
    if analysis.description.strip():
        lines.append(f"> {analysis.description.strip()}")
    analyzer = analysis.analyzer
    if analyzer.get("mode") == "mock":
        lines.append("> WARNING: MOCK MODE — findings are placeholders (no vision model was used).")
    elif analyzer:
        lines.append(f"> Analysis source: real vision model "
                     f"({analyzer.get('provider', 'unknown')} / {analyzer.get('model', 'unknown')}).")
    lines.append("")
    lines.extend(_scene_generation_prompt(analysis))
    lines.append("")
    breakdown = _scene_breakdown(analysis)
    if breakdown:
        lines.extend(breakdown)
    return "\n".join(lines).strip() + "\n"


def _scene_generation_prompt(analysis: SceneAnalysis) -> list[str]:
    lines = [
        "## Generation prompt",
        "",
        "Recreate this scene with the following visual characteristics:",
        "",
    ]
    found_any = False
    for label, key in _SCENE_GENERATION_SECTIONS:
        finding = analysis.categories.get(key)
        if finding is None:
            continue
        sentences: list[str] = []
        for inference in finding.inferences:
            text = _strip_tags(inference.strip()).rstrip(".")
            if text and not text.lower().startswith("placeholder"):
                sentences.append(f"{text}.")
        if sentences:
            found_any = True
            lines.append(f"**{label}:** {' '.join(sentences)}")
    if not found_any:
        return [
            "## Generation prompt",
            "",
            "No inferences available for this scene.",
        ]
    return lines


def _scene_breakdown(analysis: SceneAnalysis) -> list[str]:
    lines = ["## Structured breakdown", ""]
    for spec in SCENE_CATEGORIES:
        finding = analysis.categories.get(spec.key)
        if finding is None:
            continue
        if not finding.observations and not finding.inferences:
            continue
        lines.append(f"### {spec.label} (confidence: {finding.confidence:.0%})")
        lines.append("")
        if finding.observations:
            lines.append("**Observed:**")
            for observation in finding.observations:
                lines.append(f"- {_strip_tags(observation)}")
            lines.append("")
        if finding.inferences:
            lines.append("**Inferred:**")
            for inference in finding.inferences:
                lines.append(f"- {_strip_tags(inference)}")
            lines.append("")
    return lines


def _provenance_line(report: AnalysisReport) -> str:
    analyzer = report.analyzer
    if not analyzer:
        return ""
    provider = analyzer.get("provider", "unknown")
    model = analyzer.get("model", "unknown")
    if analyzer.get("mode") == "mock":
        return f"WARNING: MOCK MODE — findings are placeholders generated by {provider} (no vision model was used)."
    return f"Analysis source: real vision model ({provider} / {model})."


def _structured_breakdown(report: AnalysisReport) -> list[str]:
    lines: list[str] = []
    for spec in CATEGORIES:
        finding = report.categories.get(spec.key)
        if finding is None:
            continue
        if not finding.observations and not finding.inferences:
            continue
        lines.append(f"### {spec.label} (confidence: {finding.confidence:.0%})")
        lines.append("")
        if finding.observations:
            lines.append("**Observed:**")
            for observation in finding.observations:
                lines.append(f"- {_strip_tags(observation)}")
            lines.append("")
        if finding.inferences:
            lines.append("**Inferred:**")
            for inference in finding.inferences:
                lines.append(f"- {_strip_tags(inference)}")
            lines.append("")
    return lines


def _technical_parameters(report: AnalysisReport) -> list[str]:
    meta = report.video_metadata
    if not meta:
        return []
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    duration = float(meta.get("duration") or 0.0)
    fps = float(meta.get("fps") or 0.0)
    lines: list[str] = []
    if width and height:
        lines.append(f"- Resolution: {width}x{height} (aspect ratio {report_aspect(width, height)})")
    if duration:
        lines.append(f"- Duration: {duration:.2f} seconds")
    if fps:
        lines.append(f"- Frame rate: {fps} fps")
    lines.append(f"- Frames analyzed: {len(report.frames)}")
    return lines


def report_aspect(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "unknown"
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"
