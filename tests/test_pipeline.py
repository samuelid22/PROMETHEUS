from __future__ import annotations

import json

import pytest

from prometheus.config import PrometheusConfig
from prometheus.errors import PrometheusError
from prometheus.pipeline import PrometheusPipeline


def test_end_to_end_pipeline(synthetic_video, tmp_path):
    config = PrometheusConfig.load()
    config.sampling.frame_count = 4
    config.output.directory = tmp_path / "out"
    result = PrometheusPipeline(config=config).run(synthetic_video)

    assert len(result.frames) == 4
    assert len(result.scenes) == 1
    assert result.scenes[0].start == 0.0
    assert result.output.analysis_path.is_file()
    assert result.output.manifest_path.is_file()
    assert result.output.prompt_md_path.is_file()
    assert result.output.prompt_txt_path.is_file()

    analysis = json.loads(result.output.analysis_path.read_text(encoding="utf-8"))
    assert analysis["video_metadata"]["width"] == 320
    assert len(analysis["frames"]) == 4
    assert analysis["analyzer"]["mode"] == "mock"
    assert len(analysis["scenes"]) == 1
    assert analysis["scenes"][0]["start"] == 0.0


def test_scene_outputs_written_per_scene(cut_video, tmp_path):
    config = PrometheusConfig.load()
    config.segmentation.frames_per_scene = 2
    config.sampling.frame_count = 3
    config.output.directory = tmp_path / "out"
    result = PrometheusPipeline(config=config).run(cut_video)

    assert len(result.scenes) == 2
    scene_dirs = sorted((result.output.scenes_dir).glob("scene_*"))
    assert len(scene_dirs) == 2
    for scene_dir in scene_dirs:
        assert (scene_dir / "scene.json").is_file()
        assert (scene_dir / "reconstruction_prompt.md").is_file()
        assert (scene_dir / "reconstruction_prompt.txt").is_file()
        frames = list((scene_dir / "frames").glob("*.jpg"))
        assert len(frames) == 2

    first = json.loads((scene_dirs[0] / "scene.json").read_text(encoding="utf-8"))
    assert first["scene"]["index"] == 0
    assert first["scene"]["start"] == 0.0
    assert abs(first["scene"]["end"] - 2.0) < 0.3
    assert set(first["categories"]) == {
        "subjects", "environment", "composition", "camera", "motion",
        "lighting", "style", "graphics_text",
    }

    manifest = json.loads(result.output.manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["scenes"]) == 2
    assert all("description" in s for s in manifest["scenes"])
    assert all(len(s["frames"]) == 2 for s in manifest["scenes"])

    prompt = result.output.prompt_md_path.read_text(encoding="utf-8")
    assert "Scene timeline" in prompt
    assert "- Scene 0:" in prompt and "- Scene 1:" in prompt


def test_global_prompt_includes_scene_timeline(cut_video, tmp_path):
    config = PrometheusConfig.load()
    config.output.directory = tmp_path / "out"
    result = PrometheusPipeline(config=config).run(cut_video)
    prompt = result.prompt
    assert "## Scene timeline" in prompt
    assert "Scene 0: t=0.0s" in prompt


def test_rerun_overwrites_frames_and_scenes(cut_video, tmp_path):
    config = PrometheusConfig.load()
    config.sampling.frame_count = 2
    config.output.directory = tmp_path / "out"
    pipeline = PrometheusPipeline(config=config)
    first = pipeline.run(cut_video)
    second = pipeline.run(cut_video)
    assert second.output.run_dir == first.output.run_dir
    assert len(list(second.output.frames_dir.glob("*.jpg"))) == 2
    scene_dirs = list(second.output.scenes_dir.glob("scene_*"))
    assert len(scene_dirs) == 2


def test_pipeline_rejects_missing_video(tmp_path):
    config = PrometheusConfig.load()
    config.output.directory = tmp_path / "out"
    with pytest.raises(PrometheusError, match="not found"):
        PrometheusPipeline(config=config).run(tmp_path / "missing.mp4")
