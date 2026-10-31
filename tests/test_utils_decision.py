import math

import pytest

from mara.decision import DecisionEngine
from mara.utils import distance_3d_m, haversine_m, meters_to_degrees, parse_host_port


def test_haversine_zero_distance():
    assert haversine_m(-35.36, 149.16, -35.36, 149.16) == pytest.approx(0.0, abs=1e-6)


def test_haversine_one_degree_latitude_is_about_111_km():
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111195, rel=0.002)


def test_meters_to_degrees_roundtrip():
    d_lat, d_lon = meters_to_degrees(-35.36, 100.0, 50.0)
    distance = haversine_m(-35.36, 149.16, -35.36 + d_lat, 149.16 + d_lon)
    assert distance == pytest.approx(math.hypot(100.0, 50.0), rel=0.001)


def test_distance_3d_includes_altitude():
    assert distance_3d_m(0, 0, 0, 0, 0, 10) == pytest.approx(10.0)


def test_parse_host_port():
    assert parse_host_port("10.0.0.5:9000") == ("10.0.0.5", 9000)
    assert parse_host_port("station") == ("station", 8765)
    with pytest.raises(ValueError):
        parse_host_port("host:abc")
    with pytest.raises(ValueError):
        parse_host_port("host:70000")


def person(conf):
    return {"class": "person", "confidence": conf, "bbox": [0, 0, 10, 10]}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_decision_requires_consecutive_frames():
    engine = DecisionEngine("person", 0.6, required_frames=3)
    assert engine.evaluate([person(0.9)]) is None
    assert engine.evaluate([person(0.9)]) is None
    decision = engine.evaluate([person(0.9)])
    assert decision is not None and decision.frames_confirmed == 3


def test_decision_resets_streak_on_missing_frame():
    engine = DecisionEngine("person", 0.6, required_frames=3)
    engine.evaluate([person(0.9)])
    engine.evaluate([person(0.9)])
    engine.evaluate([])
    assert engine.evaluate([person(0.9)]) is None


def test_decision_ignores_low_confidence_and_other_classes():
    engine = DecisionEngine("person", 0.6, required_frames=1)
    assert engine.evaluate([person(0.59)]) is None
    assert engine.evaluate([{"class": "car", "confidence": 0.99, "bbox": [0, 0, 1, 1]}]) is None


def test_decision_picks_highest_confidence():
    engine = DecisionEngine("person", 0.5, required_frames=1)
    decision = engine.evaluate([person(0.7), person(0.95), person(0.8)])
    assert decision.detection["confidence"] == 0.95


def test_decision_cooldown():
    clock = FakeClock()
    engine = DecisionEngine("person", 0.5, required_frames=1, cooldown_s=30, clock=clock)
    assert engine.evaluate([person(0.9)]) is not None
    assert engine.evaluate([person(0.9)]) is None
    clock.now = 31
    assert engine.evaluate([person(0.9)]) is not None


def test_decision_is_case_insensitive():
    engine = DecisionEngine("Person", 0.5, required_frames=1)
    assert engine.evaluate([{"class": "PERSON", "confidence": 0.8, "bbox": [0, 0, 1, 1]}])
