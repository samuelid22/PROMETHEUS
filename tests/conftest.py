from __future__ import annotations

import subprocess

import pytest

from prometheus.video.tools import resolve_tool


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [resolve_tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("videos") / "synthetic.mp4"
    _run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


@pytest.fixture(scope="session")
def cut_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("videos") / "cut.mp4"
    _run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=24:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=24:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out
