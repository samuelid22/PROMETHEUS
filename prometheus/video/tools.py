from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from prometheus.errors import PrometheusError


class VideoToolError(PrometheusError):
    """An FFmpeg executable is missing or could not be started."""


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_dir = os.environ.get("PROMETHEUS_FFMPEG_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
        dirs.append(Path(env_dir) / "bin")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            dirs.extend(sorted(packages.glob("Gyan.FFmpeg*/ffmpeg*/bin"), reverse=True))
    return dirs


def resolve_tool(name: str) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""
    for directory in _candidate_dirs():
        candidate = directory / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate.resolve())
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    raise VideoToolError(
        f"{name} executable not found. Install FFmpeg with {name}, add its bin directory "
        "to PATH, or set PROMETHEUS_FFMPEG_DIR to that bin directory."
    )
