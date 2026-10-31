import csv
import os
import threading
from typing import Callable, Dict, List, Optional

from .utils import ensure_dir, utc_timestamp

FIELDNAMES = ["timestamp", "event_type", "message",
              "latitude", "longitude", "altitude", "flight_mode"]


class EventType:
    CONNECTED = "CONNECTED"
    TAKEOFF_STARTED = "TAKEOFF_STARTED"
    TAKEOFF_COMPLETED = "TAKEOFF_COMPLETED"
    WAYPOINT_STARTED = "WAYPOINT_STARTED"
    WAYPOINT_REACHED = "WAYPOINT_REACHED"
    OBJECT_DETECTED = "OBJECT_DETECTED"
    MISSION_STOPPED = "MISSION_STOPPED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    INFO = "INFO"
    ERROR = "ERROR"


def vehicle_snapshot(vehicle) -> Dict[str, Optional[object]]:
    result = {"latitude": None, "longitude": None, "altitude": None, "flight_mode": None}
    if vehicle is None:
        return result
    try:
        location = vehicle.location.global_relative_frame
        result["latitude"] = location.lat
        result["longitude"] = location.lon
        result["altitude"] = location.alt
    except Exception:
        pass
    try:
        result["flight_mode"] = vehicle.mode.name
    except Exception:
        pass
    return result


class FlightLogger:

    def __init__(self, log_dir: str = "logs", filename: str = "flight_log.csv",
                 echo: bool = True):
        self.log_dir = ensure_dir(log_dir)
        self.path = os.path.join(self.log_dir, filename)
        self.echo = echo
        self._lock = threading.Lock()
        self._listeners: List[Callable[[Dict[str, object]], None]] = []
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            with open(self.path, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(FIELDNAMES)

    def add_listener(self, listener: Callable[[Dict[str, object]], None]) -> None:
        self._listeners.append(listener)

    def log(self, event_type: str, message: str, vehicle=None) -> Dict[str, object]:
        record: Dict[str, object] = {
            "timestamp": utc_timestamp(),
            "event_type": event_type,
            "message": message,
        }
        record.update(vehicle_snapshot(vehicle))

        with self._lock:
            try:
                with open(self.path, "a", newline="", encoding="utf-8") as handle:
                    csv.DictWriter(handle, fieldnames=FIELDNAMES).writerow(record)
            except OSError as exc:
                print(f"[LOGGER] Upis u log nije uspio: {exc}")

        if self.echo:
            print(f"[{event_type}] {message}")

        for listener in list(self._listeners):
            try:
                listener(record)
            except Exception as exc:
                print(f"[LOGGER] Slušalac je prijavio grešku: {exc}")
        return record
