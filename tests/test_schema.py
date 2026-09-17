from __future__ import annotations

import pytest

from prometheus.analysis.schema import CATEGORIES, AnalysisReport, CategoryFinding
from prometheus.errors import PrometheusError


def _report_data():
    return {
        "summary": "test",
        "video_metadata": {"width": 320, "height": 240, "duration": 4.0, "fps": 24.0},
        "frames": [{"index": 0, "timestamp": 0.5}],
        "categories": {
            "subject": {
                "observations": ["A red cube at t=0.5s"],
                "inferences": ["The cube is likely the main subject"],
                "confidence": 0.8,
            },
            "lighting": {
                "observations": ["Hard shadow to the left"],
                "inferences": ["Single strong key light from the right"],
                "confidence": 0.6,
            },
        },
    }


def test_from_dict_fills_missing_categories():
    report = AnalysisReport.from_dict(_report_data())
    assert len(report.categories) == len(CATEGORIES)
    assert report.finding("subject").confidence == 0.8
    assert report.finding("scene_environment").observations == []


def test_round_trip():
    report = AnalysisReport.from_dict(_report_data())
    report.analyzer = {"provider": "gemini", "model": "gemini-2.5-flash", "mode": "real"}
    rebuilt = AnalysisReport.from_dict(report.to_dict())
    assert rebuilt.to_dict() == report.to_dict()
    assert not rebuilt.is_mock


def test_mock_flag_from_dict():
    data = _report_data()
    data["analyzer"] = {"provider": "mock", "model": "mock", "mode": "mock"}
    report = AnalysisReport.from_dict(data)
    assert report.is_mock


def test_confidence_clamped():
    data = _report_data()
    data["categories"]["subject"]["confidence"] = 5
    report = AnalysisReport.from_dict(data)
    assert report.finding("subject").confidence == 1.0


def test_validate_rejects_bad_confidence():
    report = AnalysisReport.from_dict(_report_data())
    report.finding("subject").confidence = 2.0
    with pytest.raises(PrometheusError, match="Confidence"):
        report.validate()


def test_validate_rejects_empty_entries():
    report = AnalysisReport.from_dict(_report_data())
    report.finding("subject").observations = [""]
    with pytest.raises(PrometheusError, match="Empty entry"):
        report.validate()


def test_post_init_ensures_all_categories():
    report = AnalysisReport(categories={"subject": CategoryFinding(confidence=0.5)})
    assert set(report.categories) == {spec.key for spec in CATEGORIES}
