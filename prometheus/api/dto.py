from __future__ import annotations

import json
from pathlib import Path

from prometheus.analysis.prompt_builder import report_aspect
from prometheus.analysis.schema import CATEGORIES
from prometheus.analysis.scene_schema import SCENE_CATEGORIES

_GLOBAL_LABELS = {spec.key: spec.label for spec in CATEGORIES}
_SCENE_LABELS = {spec.key: spec.label for spec in SCENE_CATEGORIES}


def build_result_dto(
    job_id: str, run_dir: Path, video_name: str, source_file: str = "source.mp4"
) -> dict:
    analysis = _load_json(run_dir / "analysis.json")
    manifest = _load_json(run_dir / "manifest.json")
    prompt_md = _read_text(run_dir / "reconstructed_prompt.md")
    meta = analysis.get("video_metadata", {})

    scenes = []
    for scene_info in manifest.get("scenes", []):
        index = scene_info["index"]
        scene_dir = run_dir / "scenes" / f"scene_{index:03d}"
        scene_analysis = _load_json(scene_dir / "scene.json")
        scene_prompt = _read_text(scene_dir / "reconstruction_prompt.md")
        frames = [
            f"/api/jobs/{job_id}/frames/scenes/scene_{index:03d}/frames/{frame['file']}"
            for frame in scene_info.get("frames", [])
        ]
        scenes.append({
            "index": index,
            "start": scene_info.get("start", 0.0),
            "end": scene_info.get("end", 0.0),
            "duration": scene_info.get("duration", 0.0),
            "description": scene_analysis.get("description", ""),
            "analyzer": scene_analysis.get("analyzer", {}),
            "prompt_markdown": scene_prompt,
            "frames": frames,
            "breakdown": _breakdown(scene_analysis.get("categories", {}), _SCENE_LABELS),
        })

    global_frames = [
        f"/api/jobs/{job_id}/frames/{frame['file']}"
        for frame in analysis.get("frames", [])
    ]

    return {
        "job_id": job_id,
        "tier": "advanced",
        "video": {
            "name": video_name,
            "duration": meta.get("duration", 0.0),
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "fps": meta.get("fps", 0.0),
            "aspect_ratio": report_aspect(int(meta.get("width") or 0), int(meta.get("height") or 0)),
            "preview_url": f"/api/jobs/{job_id}/frames/{source_file}",
        },
        "analyzer": analysis.get("analyzer", {}),
        "summary": analysis.get("summary", ""),
        "prompt_markdown": prompt_md,
        "scene_count": len(scenes),
        "scenes": scenes,
        "global_frames": global_frames,
        "breakdown": _breakdown(analysis.get("categories", {}), _GLOBAL_LABELS),
        "parameters": _parameters(meta, len(global_frames), len(scenes)),
    }


def build_basic_result_dto(
    job_id: str, run_dir: Path, video_name: str, source_file: str = "source.mp4"
) -> dict:
    inspection = _load_json(run_dir / "basic.json")
    meta = inspection["video"]
    scenes = []
    for scene in inspection.get("scenes", []):
        index = scene["index"]
        frames = [
            f"/api/jobs/{job_id}/frames/scenes/scene_{index:03d}/frames/{frame['file']}"
            for frame in scene.get("frames", [])
        ]
        scenes.append({**scene, "frames": frames, "description": "", "breakdown": [], "prompt_markdown": ""})
    return {
        "job_id": job_id,
        "tier": "basic",
        "video": {
            "name": video_name,
            "duration": meta.get("duration", 0.0),
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "fps": meta.get("fps", 0.0),
            "aspect_ratio": report_aspect(int(meta.get("width") or 0), int(meta.get("height") or 0)),
            "preview_url": f"/api/jobs/{job_id}/frames/{source_file}",
        },
        "analyzer": {"mode": "local", "provider": "ffmpeg"},
        "summary": (
            f"Local inspection found {len(scenes)} scene{'' if len(scenes) == 1 else 's'} "
            f"across {float(meta.get('duration') or 0):.1f} seconds."
        ),
        "prompt_markdown": "",
        "scene_count": len(scenes),
        "scenes": scenes,
        "global_frames": [],
        "breakdown": [],
        "parameters": _parameters(meta, sum(len(scene["frames"]) for scene in scenes), len(scenes)),
    }


def _breakdown(categories: dict, labels: dict[str, str]) -> list[dict]:
    entries = []
    for key, label in labels.items():
        finding = categories.get(key, {})
        observations = finding.get("observations", [])
        inferences = finding.get("inferences", [])
        if not observations and not inferences:
            continue
        entries.append({
            "key": key,
            "label": label,
            "confidence": finding.get("confidence", 0.0),
            "observations": observations,
            "inferences": inferences,
        })
    return entries


def _parameters(meta: dict, frame_count: int, scene_count: int) -> list[str]:
    lines: list[str] = []
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    duration = float(meta.get("duration") or 0.0)
    fps = float(meta.get("fps") or 0.0)
    if width and height:
        lines.append(f"Resolution: {width}x{height} (aspect ratio {report_aspect(width, height)})")
    if duration:
        lines.append(f"Duration: {duration:.2f} seconds")
    if fps:
        lines.append(f"Frame rate: {fps} fps")
    lines.append(f"Scenes detected: {scene_count}")
    lines.append(f"Frames analyzed: {frame_count}")
    return lines


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")
