from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prometheus.config import PrometheusConfig
from prometheus.errors import PrometheusError
from prometheus.pipeline import PrometheusPipeline
from prometheus.video.probe import probe_video


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prometheus",
        description="Prometheus: infer the creative recipe behind an AI-generated video.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="Inspect a video with ffprobe and print metadata")
    probe_parser.add_argument("video", type=Path, help="Path to the video file")

    analyze_parser = subparsers.add_parser("analyze", help="Run the full analysis pipeline")
    analyze_parser.add_argument("video", type=Path, help="Path to the MP4 video file")
    analyze_parser.add_argument("--config", type=Path, default=None, help="Path to a YAML config file")
    analyze_parser.add_argument("--strategy", choices=["uniform", "interval"], help="Frame sampling strategy")
    analyze_parser.add_argument("--frames", type=int, help="Number of frames to sample (uniform strategy)")
    analyze_parser.add_argument("--interval", type=float, help="Seconds between frames (interval strategy)")
    analyze_parser.add_argument("--max-frames", type=int, help="Hard cap on sampled frames")
    analyze_parser.add_argument("--scene-threshold", type=float, help="Scene cut score threshold (0-1)")
    analyze_parser.add_argument("--min-scene-duration", type=float, help="Minimum scene length in seconds")
    analyze_parser.add_argument("--max-scenes", type=int, help="Maximum number of scenes to analyze")
    analyze_parser.add_argument("--frames-per-scene", type=int, help="Representative frames per scene")
    analyze_parser.add_argument("--analyzer", choices=["mock", "openai", "gemini"], help="Analyzer provider")
    analyze_parser.add_argument("--model", help="Multimodal model name (openai provider)")
    analyze_parser.add_argument("--output", type=Path, help="Output directory")
    return parser


def _apply_overrides(config: PrometheusConfig, args: argparse.Namespace) -> PrometheusConfig:
    if getattr(args, "strategy", None):
        config.sampling.strategy = args.strategy
    if getattr(args, "frames", None):
        config.sampling.frame_count = args.frames
    if getattr(args, "interval", None):
        config.sampling.interval_seconds = args.interval
    if getattr(args, "scene_threshold", None):
        config.segmentation.scene_threshold = args.scene_threshold
    if getattr(args, "min_scene_duration", None):
        config.segmentation.min_scene_duration = args.min_scene_duration
    if getattr(args, "max_scenes", None):
        config.segmentation.max_scenes = args.max_scenes
    if getattr(args, "frames_per_scene", None):
        config.segmentation.frames_per_scene = args.frames_per_scene
    if getattr(args, "max_frames", None):
        config.sampling.max_frames = args.max_frames
    if getattr(args, "analyzer", None):
        config.analyzer.provider = args.analyzer
    if getattr(args, "model", None):
        config.analyzer.model = args.model
    if getattr(args, "output", None):
        config.output.directory = args.output
    return config


def _cmd_probe(args: argparse.Namespace) -> int:
    metadata = probe_video(args.video)
    print(json.dumps(metadata.to_dict(), indent=2))
    return 0


def _analyzer_line(result) -> str:
    info = result.report.analyzer or {"provider": "unknown", "model": "unknown", "mode": "unknown"}
    mode = info.get("mode", "unknown")
    if mode == "mock":
        return f"  Analyzer   : {info.get('provider')} (MOCK MODE - placeholder findings, no vision model)"
    return f"  Analyzer   : {info.get('provider')} / {info.get('model')} (real vision model)"


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = PrometheusConfig.load(getattr(args, "config", None))
    _apply_overrides(config, args)
    pipeline = PrometheusPipeline(config=config)
    result = pipeline.run(args.video)
    output = result.output
    print("Prometheus analysis complete")
    print(f"  Video      : {result.metadata.path} ({result.metadata.duration:.2f}s, "
          f"{result.metadata.width}x{result.metadata.height} @ {result.metadata.fps}fps)")
    print(f"  Frames     : {len(result.frames)} sampled ({config.sampling.strategy} strategy)")
    print(f"  Scenes     : {len(result.scenes)} detected (analyzed in {output.scenes_dir})")
    print(_analyzer_line(result))
    print(f"  Run dir    : {output.run_dir}")
    print(f"  Manifest   : {output.manifest_path}")
    print(f"  Analysis   : {output.analysis_path}")
    print(f"  Prompt     : {output.prompt_md_path}")
    print(f"  Prompt txt : {output.prompt_txt_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            return _cmd_probe(args)
        if args.command == "analyze":
            return _cmd_analyze(args)
    except PrometheusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
