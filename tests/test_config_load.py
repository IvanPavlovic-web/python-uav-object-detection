import os

import pytest

from mara.config import ConfigError, load_mission, load_mission_raw, mission_from_dict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_MISSION = os.path.join(ROOT, "config", "default_mission.yaml")


def valid_data():
    return {
        "takeoff_altitude": 10,
        "waypoints": [{"lat": -35.3629, "lon": 149.1656, "alt": 10}],
    }


def test_default_mission_has_takeoff_altitude_and_waypoints():
    raw = load_mission_raw(DEFAULT_MISSION)
    assert "takeoff_altitude" in raw
    assert "waypoints" in raw and len(raw["waypoints"]) >= 1


def test_default_mission_loads_and_validates():
    mission = load_mission(DEFAULT_MISSION)
    assert mission.takeoff_altitude == 10
    assert len(mission.waypoints) == 3
    assert mission.target_object == "person"
    assert mission.detection_action == "LOITER"
    assert mission.stop_on_detection is True
    assert 0.0 <= mission.confidence_threshold <= 1.0


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_mission(str(tmp_path / "nema.yaml"))


def test_missing_takeoff_altitude_is_rejected():
    data = valid_data()
    del data["takeoff_altitude"]
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_empty_waypoint_list_is_rejected():
    data = valid_data()
    data["waypoints"] = []
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_waypoint_without_altitude_is_rejected():
    data = valid_data()
    data["waypoints"] = [{"lat": -35.0, "lon": 149.0, "alt": None}]
    with pytest.raises(ConfigError):
        mission_from_dict(data)


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_coordinates_out_of_range_are_rejected(lat, lon):
    data = valid_data()
    data["waypoints"] = [{"lat": lat, "lon": lon, "alt": 10}]
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_invalid_action_is_rejected():
    data = valid_data()
    data["detection_action"] = "FLIP"
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_continue_mode_requires_loiter_action():
    data = valid_data()
    data["stop_on_detection"] = False
    data["detection_action"] = "RTL"
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_confidence_threshold_range():
    data = valid_data()
    data["confidence_threshold"] = 1.5
    with pytest.raises(ConfigError):
        mission_from_dict(data)


def test_invalid_yaml_syntax(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("takeoff_altitude: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_mission(str(bad))
