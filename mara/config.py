from dataclasses import dataclass
from typing import Any, Dict, Tuple

import yaml

VALID_DETECTION_ACTIONS = ("LOITER", "RTL", "LAND")
VALID_END_ACTIONS = ("RTL", "LAND", "LOITER")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Waypoint:
    name: str
    lat: float
    lon: float
    alt: float


@dataclass(frozen=True)
class Mission:
    name: str
    takeoff_altitude: float
    waypoints: Tuple[Waypoint, ...]
    target_object: str = "person"
    confidence_threshold: float = 0.60
    required_consecutive_frames: int = 3
    detection_action: str = "LOITER"
    stop_on_detection: bool = True
    resume_hold_seconds: float = 5.0
    detection_cooldown_seconds: float = 30.0
    waypoint_radius_m: float = 2.0
    waypoint_timeout_s: float = 120.0
    end_action: str = "RTL"


def load_mission_raw(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Datoteka misije ne postoji: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Neispravan YAML u {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Korijen YAML datoteke mora biti rječnik (mapiranje).")
    return data


def _number(data: Dict[str, Any], key: str, minimum: float = None,
            maximum: float = None, where: str = "misija") -> float:
    if key not in data:
        raise ConfigError(f"Nedostaje obavezno polje '{key}' ({where}).")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"Polje '{key}' ({where}) mora biti broj, a dobijeno je: {value!r}")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConfigError(f"Polje '{key}' ({where}) mora biti >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"Polje '{key}' ({where}) mora biti <= {maximum}.")
    return value


def mission_from_dict(data: Dict[str, Any]) -> Mission:
    takeoff = _number(data, "takeoff_altitude", minimum=1.0, maximum=120.0)

    raw_points = data.get("waypoints")
    if not isinstance(raw_points, list) or not raw_points:
        raise ConfigError("Polje 'waypoints' mora biti neprazna lista.")

    waypoints = []
    for index, item in enumerate(raw_points, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"Waypoint #{index} mora biti rječnik.")
        where = f"waypoint #{index}"
        waypoints.append(Waypoint(
            name=str(item.get("name", f"WP{index}")),
            lat=_number(item, "lat", -90.0, 90.0, where),
            lon=_number(item, "lon", -180.0, 180.0, where),
            alt=_number(item, "alt", 1.0, 120.0, where),
        ))

    action = str(data.get("detection_action", "LOITER")).upper()
    if action not in VALID_DETECTION_ACTIONS:
        raise ConfigError(f"detection_action mora biti jedno od {VALID_DETECTION_ACTIONS}.")
    end_action = str(data.get("end_action", "RTL")).upper()
    if end_action not in VALID_END_ACTIONS:
        raise ConfigError(f"end_action mora biti jedno od {VALID_END_ACTIONS}.")

    stop = data.get("stop_on_detection", True)
    if not isinstance(stop, bool):
        raise ConfigError("stop_on_detection mora biti true ili false.")
    if not stop and action != "LOITER":
        raise ConfigError("Ako misija nastavlja nakon detekcije, detection_action mora biti LOITER.")

    frames = data.get("required_consecutive_frames", 3)
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        raise ConfigError("required_consecutive_frames mora biti cio broj >= 1.")

    def optional(key: str, default: float, minimum: float, maximum: float = None) -> float:
        return _number({key: data.get(key, default)}, key, minimum, maximum)

    return Mission(
        name=str(data.get("mission_name", "MARA-1 misija")),
        takeoff_altitude=takeoff,
        waypoints=tuple(waypoints),
        target_object=str(data.get("target_object", "person")).strip().lower(),
        confidence_threshold=optional("confidence_threshold", 0.60, 0.0, 1.0),
        required_consecutive_frames=frames,
        detection_action=action,
        stop_on_detection=stop,
        resume_hold_seconds=optional("resume_hold_seconds", 5.0, 0.0),
        detection_cooldown_seconds=optional("detection_cooldown_seconds", 30.0, 0.0),
        waypoint_radius_m=optional("waypoint_radius_m", 2.0, 0.5),
        waypoint_timeout_s=optional("waypoint_timeout_s", 120.0, 5.0),
        end_action=end_action,
    )


def load_mission(path: str) -> Mission:
    return mission_from_dict(load_mission_raw(path))
