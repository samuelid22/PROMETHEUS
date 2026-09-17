from __future__ import annotations

import re
import subprocess

from prometheus.errors import PrometheusError
from prometheus.video.tools import VideoToolError

_CUT_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_DEDUP_WINDOW = 0.2


def detect_scene_cuts(ffmpeg: str, video_path: str, duration: float, threshold: float) -> list[float]:
    cmd = [
        ffmpeg,
        "-nostdin",
        "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except OSError as exc:
        raise VideoToolError(f"Could not start ffmpeg executable {ffmpeg}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrometheusError(f"Scene detection timed out for {video_path}") from exc
    if result.returncode != 0:
        raise PrometheusError(f"Scene detection failed for {video_path}: {result.stderr.strip()}")
    timestamps = sorted({float(m) for m in _CUT_RE.findall(result.stderr) if 0 <= float(m) < duration})
    deduped: list[float] = []
    for t in timestamps:
        if not deduped or t - deduped[-1] > _DEDUP_WINDOW:
            deduped.append(t)
    return deduped
