from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prometheus.analysis.analyzer import MultimodalAnalyzer, create_analyzer
from prometheus.analysis.prompt_builder import (
    build_reconstructed_prompt,
    build_scene_reconstruction_prompt,
)
from prometheus.analysis.schema import AnalysisReport
from prometheus.config import PrometheusConfig
from prometheus.errors import PrometheusError
from prometheus.storage import (
    RunOutput,
    prepare_run_directory,
    prepare_scene_directory,
    save_analysis,
    save_manifest,
    save_prompt,
    save_scene_analysis,
    save_scene_prompt,
)
from prometheus.video.probe import VideoMetadata, probe_video
from prometheus.video.sampler import FrameSampler, SampledFrame
from prometheus.video.segmenter import Scene, SceneSegmenter


@dataclass
class PipelineResult:
    metadata: VideoMetadata
    frames: list[SampledFrame]
    scenes: list[Scene]
    report: AnalysisReport
    prompt: str
    output: RunOutput


class PrometheusPipeline:
    def __init__(
        self,
        config: PrometheusConfig | None = None,
        analyzer: MultimodalAnalyzer | None = None,
    ):
        self.config = config or PrometheusConfig()
        self.analyzer = analyzer or create_analyzer(self.config.analyzer)
        self.sampler = FrameSampler(self.config.sampling)
        self.segmenter = SceneSegmenter(self.config.segmentation)

    def run(
        self,
        video_path: Path | str,
        progress: Callable[[str], None] | None = None,
    ) -> PipelineResult:
        notify = progress or (lambda message: None)
        config = self.config
        config.validate()
        notify("Inspecting video")
        metadata = probe_video(video_path)
        run_dir, frames_dir = prepare_run_directory(video_path, config.output.directory)
        notify("Detecting scene cuts")
        scenes = self.segmenter.segment(metadata)
        scene_summaries = self._analyze_scenes(metadata, scenes, run_dir, notify)
        notify("Sampling representative frames")
        frames = self.sampler.sample(metadata, frames_dir)
        if not frames:
            raise PrometheusError(f"No frames could be extracted from {metadata.path}")
        notify("Analyzing shared characteristics")
        report = self.analyzer.analyze(metadata, frames, scene_context=scene_summaries)
        report.scenes = scene_summaries
        notify("Saving results")
        prompt = build_reconstructed_prompt(report)

        manifest_path = save_manifest(run_dir, metadata, frames, config.sampling, scenes=scene_summaries)
        analysis_path = save_analysis(run_dir, report)
        prompt_md_path, prompt_txt_path = save_prompt(run_dir, prompt)
        output = RunOutput(
            run_dir=run_dir,
            frames_dir=frames_dir,
            scenes_dir=run_dir / "scenes",
            manifest_path=manifest_path,
            analysis_path=analysis_path,
            prompt_md_path=prompt_md_path,
            prompt_txt_path=prompt_txt_path,
        )
        return PipelineResult(
            metadata=metadata,
            frames=frames,
            scenes=scenes,
            report=report,
            prompt=prompt,
            output=output,
        )

    def _analyze_scenes(
        self,
        metadata: VideoMetadata,
        scenes: list[Scene],
        run_dir: Path,
        notify: Callable[[str], None],
    ) -> list[dict]:
        summaries: list[dict] = []
        total = len(scenes)
        for scene in scenes:
            notify(f"Analyzing scene {scene.index + 1} of {total}")
            scene_dir = prepare_scene_directory(run_dir, scene)
            frames = self.sampler.sample_range(
                metadata,
                scene.start,
                scene.end,
                self.config.segmentation.frames_per_scene,
                scene_dir / "frames",
            )
            analysis = self.analyzer.analyze_scene(metadata, scene, frames)
            prompt = build_scene_reconstruction_prompt(analysis)
            analysis_path = save_scene_analysis(scene_dir, analysis)
            prompt_md_path, _ = save_scene_prompt(scene_dir, prompt)
            scene_info = scene.to_dict()
            scene_info["description"] = analysis.description
            scene_info["frames"] = [frame.to_dict() for frame in frames]
            scene_info["analysis_file"] = str(analysis_path.relative_to(run_dir))
            scene_info["prompt_file"] = str(prompt_md_path.relative_to(run_dir))
            summaries.append(scene_info)
        return summaries
