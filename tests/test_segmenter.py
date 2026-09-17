from __future__ import annotations

from prometheus.config import SegmentationConfig
from prometheus.video.probe import probe_video
from prometheus.video.segmenter import SceneSegmenter


def _segment(video, **overrides):
    config = SegmentationConfig(**overrides)
    return SceneSegmenter(config).segment(probe_video(video))


def test_hard_cut_yields_two_scenes(cut_video):
    scenes = _segment(cut_video)
    assert len(scenes) == 2
    assert scenes[0].start == 0.0
    assert abs(scenes[0].end - 2.0) < 0.3
    assert abs(scenes[1].start - 2.0) < 0.3
    assert abs(scenes[1].end - 4.0) < 0.05
    assert scenes[0].index == 0 and scenes[1].index == 1


def test_no_cuts_yields_single_scene(synthetic_video):
    scenes = _segment(synthetic_video)
    assert len(scenes) == 1
    assert scenes[0].start == 0.0
    assert abs(scenes[0].end - 4.0) < 0.5


def test_max_scenes_merges_to_limit(cut_video):
    scenes = _segment(cut_video, max_scenes=1)
    assert len(scenes) == 1
    assert scenes[0].start == 0.0
    assert abs(scenes[0].end - 4.0) < 0.05


def test_min_scene_duration_filters_short_scenes(cut_video):
    scenes = _segment(cut_video, min_scene_duration=5.0)
    assert len(scenes) == 1
    assert abs(scenes[0].end - 4.0) < 0.05


def test_scenes_cover_whole_video_without_gaps(cut_video):
    scenes = _segment(cut_video)
    for previous, current in zip(scenes, scenes[1:]):
        assert abs(previous.end - current.start) < 0.01


def test_scene_duration_property(cut_video):
    scenes = _segment(cut_video)
    assert all(s.duration > 0 for s in scenes)
