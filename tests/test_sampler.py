from __future__ import annotations

from prometheus.config import SamplingConfig
from prometheus.video.probe import probe_video
from prometheus.video.sampler import FrameSampler


def _sample(tmp_path, video, **overrides):
    config = SamplingConfig(**overrides)
    return FrameSampler(config).sample(probe_video(video), tmp_path / "frames")


def test_uniform_sampling_count_and_files(tmp_path, synthetic_video):
    frames = _sample(tmp_path, synthetic_video, strategy="uniform", frame_count=6)
    assert len(frames) == 6
    for frame in frames:
        assert frame.path.is_file()
        assert frame.path.stat().st_size > 0
        assert frame.path.suffix == ".jpg"
        assert "t" in frame.path.name


def test_uniform_timestamps_ordered_and_within_duration(tmp_path, synthetic_video):
    duration = probe_video(synthetic_video).duration
    frames = _sample(tmp_path, synthetic_video, strategy="uniform", frame_count=5)
    timestamps = [f.timestamp for f in frames]
    assert timestamps == sorted(timestamps)
    assert all(0 <= t <= duration for t in timestamps)


def test_interval_sampling(tmp_path, synthetic_video):
    frames = _sample(tmp_path, synthetic_video, strategy="interval", interval_seconds=1.0)
    assert 3 <= len(frames) <= 5
    timestamps = [f.timestamp for f in frames]
    assert all(b - a >= 0.9 for a, b in zip(timestamps, timestamps[1:]))


def test_max_frames_cap(tmp_path, synthetic_video):
    frames = _sample(tmp_path, synthetic_video, strategy="interval", interval_seconds=0.5, max_frames=4)
    assert len(frames) == 4


def test_sample_range_frames_within_scene(tmp_path, synthetic_video):
    metadata = probe_video(synthetic_video)
    frames = FrameSampler(SamplingConfig()).sample_range(
        metadata, start=1.0, end=3.0, count=3, output_dir=tmp_path / "scene_frames"
    )
    assert len(frames) == 3
    assert all(1.0 <= f.timestamp <= 3.0 for f in frames)
    assert all(f.path.is_file() and f.path.stat().st_size > 0 for f in frames)


def test_sample_range_single_frame(tmp_path, synthetic_video):
    metadata = probe_video(synthetic_video)
    frames = FrameSampler(SamplingConfig()).sample_range(
        metadata, start=0.0, end=metadata.duration, count=1, output_dir=tmp_path / "whole"
    )
    assert len(frames) == 1
    assert abs(frames[0].timestamp - metadata.duration / 2) < 0.01
