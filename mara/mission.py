import time
from typing import Callable, Optional

from .config import Mission, Waypoint
from .decision import DecisionEngine
from .logger import EventType, FlightLogger
from .utils import distance_3d_m
from .vehicle import (VehicleError, arm_and_takeoff, get_position, make_location,
                      set_mode)
from .vision import VisionPipeline, save_snapshot

REACHED = "REACHED"
INTERRUPTED = "INTERRUPTED"
TIMEOUT = "TIMEOUT"

TICK_STOP = "STOP"
TICK_RESUMED = "RESUMED"

RESULT_COMPLETED = "COMPLETED"
RESULT_STOPPED = "STOPPED_ON_DETECTION"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_ERROR = "ERROR"
RESULT_USER_INTERRUPT = "USER_INTERRUPT"


def distance_to_waypoint(vehicle, waypoint: Waypoint) -> float:
    lat, lon, alt = get_position(vehicle)
    return distance_3d_m(lat, lon, alt, waypoint.lat, waypoint.lon, waypoint.alt)


def fly_to_waypoint(vehicle, waypoint: Waypoint, radius_m: float = 2.0,
                    timeout_s: float = 120.0,
                    on_tick: Optional[Callable[[], Optional[str]]] = None,
                    poll_interval_s: float = 1.0) -> str:
    target = make_location(waypoint.lat, waypoint.lon, waypoint.alt)
    vehicle.simple_goto(target)
    started = time.monotonic()

    while True:
        if distance_to_waypoint(vehicle, waypoint) <= radius_m:
            return REACHED
        if time.monotonic() - started > timeout_s:
            return TIMEOUT
        if on_tick is not None:
            outcome = on_tick()
            if outcome == TICK_STOP:
                return INTERRUPTED
            if outcome == TICK_RESUMED:
                vehicle.simple_goto(target)
        time.sleep(poll_interval_s)


class MissionRunner:

    def __init__(self, vehicle, mission: Mission, logger: FlightLogger,
                 pipeline: Optional[VisionPipeline] = None,
                 decision: Optional[DecisionEngine] = None,
                 poll_interval_s: float = 1.0):
        self.vehicle = vehicle
        self.mission = mission
        self.logger = logger
        self.pipeline = pipeline
        self.decision = decision or DecisionEngine.from_mission(mission)
        self.poll_interval_s = poll_interval_s

    def _tick(self) -> Optional[str]:
        if self.pipeline is None:
            return None
        polled = self.pipeline.poll()
        if polled is None:
            return None
        frame, detections = polled
        decision = self.decision.evaluate(detections)
        if decision is None:
            return None

        det = decision.detection
        set_mode(self.vehicle, decision.action)
        snapshot = save_snapshot(frame, det, self.logger.log_dir)
        self.logger.log(
            EventType.OBJECT_DETECTED,
            f"{det['class']} pouzdanost={float(det['confidence']):.2f} bbox={det['bbox']} "
            f"potvrda_frame={decision.frames_confirmed} akcija={decision.action} "
            f"snimak={snapshot or 'nije sacuvan'}",
            self.vehicle)

        if decision.stop_mission:
            return TICK_STOP

        self.logger.log(EventType.INFO,
                        f"Lebdim {self.mission.resume_hold_seconds:.0f} s, zatim nastavljam misiju.",
                        self.vehicle)
        time.sleep(self.mission.resume_hold_seconds)
        set_mode(self.vehicle, "GUIDED")
        self.decision.reset()
        return TICK_RESUMED

    def run(self) -> str:
        m = self.mission
        self.logger.log(EventType.INFO, f"Početak misije: {m.name}", self.vehicle)
        try:
            arm_and_takeoff(self.vehicle, m.takeoff_altitude, self.logger)

            for waypoint in m.waypoints:
                self.logger.log(
                    EventType.WAYPOINT_STARTED,
                    f"{waypoint.name}: lat={waypoint.lat:.6f} lon={waypoint.lon:.6f} "
                    f"alt={waypoint.alt:.1f}", self.vehicle)

                outcome = fly_to_waypoint(self.vehicle, waypoint, m.waypoint_radius_m,
                                          m.waypoint_timeout_s, self._tick,
                                          self.poll_interval_s)

                if outcome == REACHED:
                    self.logger.log(EventType.WAYPOINT_REACHED, waypoint.name, self.vehicle)
                elif outcome == INTERRUPTED:
                    self.logger.log(
                        EventType.MISSION_STOPPED,
                        f"Misija zaustavljena nakon detekcije, režim: {self.vehicle.mode.name}",
                        self.vehicle)
                    return RESULT_STOPPED
                else:
                    self.logger.log(EventType.ERROR,
                                    f"Isteklo vrijeme za {waypoint.name}, prekidam misiju.",
                                    self.vehicle)
                    self._failsafe()
                    return RESULT_TIMEOUT

            set_mode(self.vehicle, m.end_action)
            self.logger.log(EventType.MISSION_COMPLETED,
                            f"Misija završena, režim: {m.end_action}", self.vehicle)
            return RESULT_COMPLETED

        except KeyboardInterrupt:
            self.logger.log(EventType.MISSION_STOPPED,
                            "Prekid od strane operatera, prelazim u LOITER.", self.vehicle)
            self._hold()
            return RESULT_USER_INTERRUPT
        except VehicleError as exc:
            self.logger.log(EventType.ERROR, str(exc), self.vehicle)
            self._failsafe()
            return RESULT_ERROR
        except Exception as exc:
            self.logger.log(EventType.ERROR, f"Neočekivana greška: {exc!r}", self.vehicle)
            self._failsafe()
            return RESULT_ERROR

    def _hold(self) -> None:
        try:
            set_mode(self.vehicle, "LOITER")
        except Exception as exc:
            self.logger.log(EventType.ERROR, f"LOITER nije postavljen: {exc}", self.vehicle)

    def _failsafe(self) -> None:
        try:
            set_mode(self.vehicle, "RTL")
            self.logger.log(EventType.INFO, "Aktiviran povratak na polazište (RTL).", self.vehicle)
        except Exception as exc:
            self.logger.log(EventType.ERROR, f"RTL nije postavljen: {exc}", self.vehicle)
