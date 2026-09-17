from __future__ import annotations

import pytest
import shutil

from prometheus.errors import PrometheusError
from prometheus.video.probe import probe_video
from prometheus.video.tools import VideoToolError


def test_probe_returns_expected_metadata(synthetic_video):
    metadata = probe_video(synthetic_video)
    assert metadata.width == 320
    assert metadata.height == 240
    assert abs(metadata.duration - 4.0) < 0.5
    assert abs(metadata.fps - 24.0) < 0.1
    assert metadata.codec == "h264"
    assert metadata.path == str(synthetic_video)


def test_probe_aspect_ratio(synthetic_video):
    metadata = probe_video(synthetic_video)
    assert metadata.aspect_ratio == "4:3"


def test_probe_missing_file():
    with pytest.raises(PrometheusError, match="not found"):
        probe_video("does_not_exist.mp4")


def test_probe_reports_missing_ffprobe_separately(monkeypatch, tmp_path):
    from prometheus.video import tools

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(tools, "_candidate_dirs", lambda: [])
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    with pytest.raises(VideoToolError, match="ffprobe executable not found"):
        probe_video(video)


def test_probe_reports_ffprobe_launch_failure(monkeypatch, tmp_path):
    from prometheus.video import probe

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(probe, "resolve_tool", lambda name: "ffprobe")
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified", "ffprobe")
    monkeypatch.setattr(probe.subprocess, "run", missing)
    with pytest.raises(VideoToolError, match="ffprobe executable not found"):
        probe_video(video)


@pytest.mark.parametrize("tool_name", ["ffmpeg", "ffprobe"])
def test_missing_video_tool_is_named(monkeypatch, tool_name):
    from prometheus.video import tools

    monkeypatch.setattr(tools, "_candidate_dirs", lambda: [])
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    with pytest.raises(VideoToolError, match=f"{tool_name} executable not found"):
        tools.resolve_tool(tool_name)


def test_probe_handles_video_path_with_spaces(synthetic_video, tmp_path):
    folder = tmp_path / "folder with spaces"
    folder.mkdir()
    video = folder / "uploaded video.mp4"
    shutil.copyfile(synthetic_video, video)
    assert probe_video(video).path == str(video)
