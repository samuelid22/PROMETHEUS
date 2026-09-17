from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from prometheus.config import SamplingConfig
from prometheus.errors import PrometheusError
from prometheus.video.probe import VideoMetadata
from prometheus.video.tools import VideoToolError, resolve_tool


@dataclass
class SampledFrame:
    index: int
    timestamp: float
    path: Path

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": round(self.timestamp, 3),
            "file": self.path.name,
        }


def uniform_timestamps(start: float, end: float, count: int) -> list[float]:
    count = max(1, count)
    return [start + (end - start) * (i + 0.5) / count for i in range(count)]


def frame_name(index: int, timestamp: float, image_format: str) -> str:
    return f"frame_{index:03d}_t{timestamp:08.3f}s.{image_format}"


def cap_evenly(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def extract_frame(
    ffmpeg: str,
    video_path: str,
    timestamp: float,
    destination: Path,
    image_format: str,
    image_quality: int,
) -> None:
    cmd = [
        ffmpeg,
        "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", video_path,
        "-frames:v", "1",
    ]
    if image_format == "jpg":
        cmd += ["-q:v", str(image_quality)]
    cmd.append(str(destination))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise VideoToolError(f"Could not start ffmpeg executable {ffmpeg}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrometheusError(f"Frame extraction timed out at t={timestamp:.3f}s") from exc
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = result.stderr.strip() or "no output frame produced"
        raise PrometheusError(f"Frame extraction failed at t={timestamp:.3f}s: {detail}")


class FrameSampler:
    def __init__(self, config: SamplingConfig):
        self.config = config
        self._ffmpeg = resolve_tool("ffmpeg")

    def sample(self, video: VideoMetadata, output_dir: Path) -> list[SampledFrame]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return self._extract_all(video, self._plan(video), output_dir)

    def sample_range(
        self,
        video: VideoMetadata,
        start: float,
        end: float,
        count: int,
        output_dir: Path,
    ) -> list[SampledFrame]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return self._extract_all(video, uniform_timestamps(start, end, count), output_dir)

    def _extract_all(
        self, video: VideoMetadata, timestamps: list[float], output_dir: Path
    ) -> list[SampledFrame]:
        frames: list[SampledFrame] = []
        for index, timestamp in enumerate(timestamps):
            path = output_dir / frame_name(index, timestamp, self.config.image_format)
            extract_frame(
                self._ffmpeg,
                video.path,
                timestamp,
                path,
                self.config.image_format,
                self.config.image_quality,
            )
            frames.append(SampledFrame(index=index, timestamp=timestamp, path=path))
        return frames

    def _plan(self, video: VideoMetadata) -> list[float]:
        if self.config.strategy == "uniform":
            timestamps = uniform_timestamps(0.0, video.duration, self.config.frame_count)
            return cap_evenly(timestamps, self.config.max_frames)
        if self.config.strategy == "interval":
            return self._interval_timestamps(video.duration, self.config.interval_seconds)
        raise PrometheusError(f"Unknown sampling strategy: {self.config.strategy}")

    def _interval_timestamps(self, duration: float, interval: float) -> list[float]:
        step_count = int(duration / interval) + (1 if duration % interval else 0)
        timestamps = [(i + 0.5) * interval for i in range(max(1, step_count))]
        timestamps = [t for t in timestamps if t < duration]
        if not timestamps:
            timestamps = [duration / 2]
        return cap_evenly(timestamps, self.config.max_frames)
