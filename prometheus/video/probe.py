from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from prometheus.errors import PrometheusError
from prometheus.video.tools import VideoToolError, resolve_tool


@dataclass
class VideoMetadata:
    path: str
    container: str
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    pixel_format: str
    bitrate: int
    frame_count: int | None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def aspect_ratio(self) -> str:
        if self.width <= 0 or self.height <= 0:
            return "unknown"
        a, b = self.width, self.height
        while b:
            a, b = b, a % b
        return f"{self.width // a}:{self.height // a}"


def probe_video(video_path: Path | str) -> VideoMetadata:
    path = Path(video_path).resolve()
    if not path.is_file():
        raise PrometheusError(f"Video file not found: {path}")
    cmd = [
        resolve_tool("ffprobe"),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise VideoToolError(f"ffprobe executable not found: {cmd[0]}") from exc
    except OSError as exc:
        raise VideoToolError(f"Could not start ffprobe executable {cmd[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrometheusError(f"ffprobe timed out for {path}") from exc
    if result.returncode != 0:
        raise PrometheusError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PrometheusError(f"Could not parse ffprobe output for {path}: {exc}") from exc

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise PrometheusError(f"No video stream found in {path}")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
    if duration <= 0:
        raise PrometheusError(f"Could not determine duration for {path}")

    fps = _parse_frame_rate(video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "0/1")
    if fps <= 0:
        fps = 25.0

    nb_frames = video_stream.get("nb_frames")
    return VideoMetadata(
        path=str(path),
        container=fmt.get("format_name", "unknown"),
        duration=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        fps=round(fps, 3),
        codec=video_stream.get("codec_name", "unknown"),
        pixel_format=video_stream.get("pix_fmt", "unknown"),
        bitrate=int(fmt.get("bit_rate") or 0),
        frame_count=int(nb_frames) if nb_frames and nb_frames.isdigit() else None,
    )


def _parse_frame_rate(rate: str) -> float:
    try:
        num, _, den = rate.partition("/")
        numerator = float(num)
        denominator = float(den) if den else 1.0
        if denominator == 0:
            return 0.0
        return numerator / denominator
    except ValueError:
        return 0.0
