import csv

import pytest

from mara.config import Mission, Waypoint
from mara.decision import DecisionEngine
from mara.logger import FlightLogger
from mara.mission import (INTERRUPTED, REACHED, RESULT_COMPLETED, RESULT_STOPPED,
                          RESULT_TIMEOUT, MissionRunner, distance_to_waypoint,
                          fly_to_waypoint)
from mara.simulated import SimulatedVehicle
from mara.utils import meters_to_degrees
from mara.vehicle import arm_and_takeoff
from mara.vision import ScriptedDetector, SyntheticFrameSource, VisionPipeline

HOME = (-35.363261, 149.165230)
SCALE = 8.0


def waypoint_at(north_m, east_m, alt=10.0, name="WP"):
    d_lat, d_lon = meters_to_degrees(HOME[0], north_m, east_m)
    return Waypoint(name, HOME[0] + d_lat, HOME[1] + d_lon, alt)


def make_mission(**overrides):
    values = dict(
        name="test", takeoff_altitude=10.0,
        waypoints=(waypoint_at(60, 0, name="WP1"), waypoint_at(60, 60, name="WP2")),
        target_object="person", confidence_threshold=0.6,
        required_consecutive_frames=2, detection_action="LOITER",
        stop_on_detection=True, resume_hold_seconds=0.1,
        detection_cooldown_seconds=60.0, waypoint_radius_m=2.0,
        waypoint_timeout_s=30.0, end_action="RTL")
    values.update(overrides)
    return Mission(**values)


@pytest.fixture
def vehicle():
    v = SimulatedVehicle(time_scale=SCALE)
    yield v
    v.close()


@pytest.fixture
def logger(tmp_path):
    return FlightLogger(str(tmp_path / "logs"), echo=False)


def read_events(logger):
    with open(logger.path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def event_types(logger):
    return [row["event_type"] for row in read_events(logger)]


def pipeline_with(appear_after_s):
    return VisionPipeline(SyntheticFrameSource(),
                          ScriptedDetector("person", appear_after_s), min_interval_s=0.02)


def test_arm_and_takeoff_reaches_95_percent(vehicle, logger):
    arm_and_takeoff(vehicle, 10.0, logger)
    assert vehicle.armed
    assert vehicle.mode.name == "GUIDED"
    assert vehicle.location.global_relative_frame.alt >= 9.5


def test_fly_to_waypoint_reaches_target(vehicle, logger):
    arm_and_takeoff(vehicle, 10.0, logger)
    wp = waypoint_at(50, 0)
    assert fly_to_waypoint(vehicle, wp, 2.0, 30, poll_interval_s=0.05) == REACHED
    assert distance_to_waypoint(vehicle, wp) <= 2.0


def test_fly_to_waypoint_can_be_interrupted(vehicle, logger):
    arm_and_takeoff(vehicle, 10.0, logger)
    wp = waypoint_at(200, 0)
    assert fly_to_waypoint(vehicle, wp, 2.0, 30, on_tick=lambda: "STOP",
                           poll_interval_s=0.05) == INTERRUPTED


def test_mission_without_vision_completes(vehicle, logger):
    runner = MissionRunner(vehicle, make_mission(), logger, pipeline=None, poll_interval_s=0.05)
    assert runner.run() == RESULT_COMPLETED
    events = event_types(logger)
    for expected in ("TAKEOFF_STARTED", "TAKEOFF_COMPLETED", "WAYPOINT_STARTED",
                     "WAYPOINT_REACHED", "MISSION_COMPLETED"):
        assert expected in events
    assert events.count("WAYPOINT_REACHED") == 2
    assert vehicle.mode.name == "RTL"
    assert "OBJECT_DETECTED" not in events


def test_detection_stops_mission_and_holds_position(vehicle, logger):
    runner = MissionRunner(vehicle, make_mission(), logger, pipeline_with(0.2),
                           poll_interval_s=0.05)
    assert runner.run() == RESULT_STOPPED
    events = event_types(logger)
    assert "OBJECT_DETECTED" in events and "MISSION_STOPPED" in events
    assert "MISSION_COMPLETED" not in events
    assert vehicle.mode.name == "LOITER"
    p1 = vehicle.location.global_relative_frame.lat
    import time
    time.sleep(0.3)
    assert vehicle.location.global_relative_frame.lat == pytest.approx(p1, abs=1e-9)


def test_detection_row_contains_position_and_mode(vehicle, logger):
    MissionRunner(vehicle, make_mission(), logger, pipeline_with(0.2),
                  poll_interval_s=0.05).run()
    row = next(r for r in read_events(logger) if r["event_type"] == "OBJECT_DETECTED")
    assert row["latitude"] and row["longitude"] and row["altitude"]
    assert row["flight_mode"] == "LOITER"
    assert "person" in row["message"]


def test_continue_mode_resumes_and_finishes(vehicle, logger):
    mission = make_mission(stop_on_detection=False)
    runner = MissionRunner(vehicle, mission, logger, pipeline_with(0.2), poll_interval_s=0.05)
    assert runner.run() == RESULT_COMPLETED
    events = event_types(logger)
    assert events.count("OBJECT_DETECTED") == 1
    assert events.count("WAYPOINT_REACHED") == 2


def test_timeout_triggers_failsafe_rtl(vehicle, logger):
    far = waypoint_at(5000, 0, name="DALEKO")
    mission = make_mission(waypoints=(far,), waypoint_timeout_s=0.5)
    result = MissionRunner(vehicle, mission, logger, poll_interval_s=0.05).run()
    assert result == RESULT_TIMEOUT
    assert "ERROR" in event_types(logger)
    assert vehicle.mode.name == "RTL"


def test_csv_log_has_required_columns(vehicle, logger):
    MissionRunner(vehicle, make_mission(waypoints=(waypoint_at(30, 0),)), logger,
                  poll_interval_s=0.05).run()
    with open(logger.path, newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == ["timestamp", "event_type", "message",
                      "latitude", "longitude", "altitude", "flight_mode"]


def test_listener_receives_every_event(vehicle, logger):
    received = []
    logger.add_listener(received.append)
    MissionRunner(vehicle, make_mission(waypoints=(waypoint_at(30, 0),)), logger,
                  poll_interval_s=0.05).run()
    assert len(received) == len(read_events(logger))


def test_decision_engine_can_be_injected(vehicle, logger):
    engine = DecisionEngine("person", 0.6, required_frames=1, action="LOITER",
                            stop_mission=True)
    runner = MissionRunner(vehicle, make_mission(), logger, pipeline_with(0.0), engine,
                           poll_interval_s=0.05)
    assert runner.run() == RESULT_STOPPED
