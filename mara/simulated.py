import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .utils import distance_3d_m, haversine_m, meters_to_degrees


@dataclass
class SimMode:
    name: str


@dataclass
class SimLocation:
    lat: float
    lon: float
    alt: float


class _Locations:
    def __init__(self, frame: SimLocation):
        self.global_relative_frame = frame


class _Gps:
    fix_type = 3
    satellites_visible = 10


class _Battery:
    voltage = 12.4
    level = 100


class SimulatedVehicle:

    def __init__(self, home=(-35.363261, 149.165230), speed_mps: float = 5.0,
                 climb_mps: float = 2.5, time_scale: float = 1.0,
                 prearm_delay_s: float = 0.0):
        self._home = SimLocation(home[0], home[1], 0.0)
        self._position = SimLocation(home[0], home[1], 0.0)
        self.location = _Locations(self._position)
        self.gps_0 = _Gps()
        self.battery = _Battery()
        self.speed_mps = speed_mps
        self.climb_mps = climb_mps
        self.time_scale = time_scale
        self._ready_at = time.monotonic() + prearm_delay_s / max(time_scale, 1e-6)
        self._mode = SimMode("STABILIZE")
        self._armed = False
        self._target: Optional[SimLocation] = None
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="sim-vehicle", daemon=True)
        self._thread.start()

    @property
    def is_armable(self) -> bool:
        return time.monotonic() >= self._ready_at

    @property
    def armed(self) -> bool:
        return self._armed

    @armed.setter
    def armed(self, value: bool) -> None:
        if value and not self.is_armable:
            return
        self._armed = bool(value)

    @property
    def mode(self) -> SimMode:
        return self._mode

    @mode.setter
    def mode(self, value) -> None:
        name = getattr(value, "name", value)
        with self._lock:
            self._mode = SimMode(str(name).upper())
            if self._mode.name == "RTL":
                self._target = SimLocation(self._home.lat, self._home.lon,
                                           max(self._position.alt, 10.0))
            elif self._mode.name in ("LOITER", "LAND"):
                self._target = None

    @property
    def home_location(self) -> SimLocation:
        return self._home

    def simple_takeoff(self, altitude: float) -> None:
        with self._lock:
            if self._armed and self._mode.name == "GUIDED":
                self._target = SimLocation(self._position.lat, self._position.lon,
                                           float(altitude))

    def simple_goto(self, location) -> None:
        with self._lock:
            if self._mode.name == "GUIDED":
                self._target = SimLocation(location.lat, location.lon, location.alt)

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        last = time.monotonic()
        while self._running:
            time.sleep(0.02)
            now = time.monotonic()
            dt = (now - last) * self.time_scale
            last = now
            with self._lock:
                self._step(dt)

    def _step(self, dt: float) -> None:
        pos = self._position
        if not self._armed:
            return
        mode = self._mode.name
        if mode == "LAND" or (mode == "RTL" and self._target is not None
                              and haversine_m(pos.lat, pos.lon, self._home.lat,
                                              self._home.lon) < 1.0):
            pos.alt = max(0.0, pos.alt - self.climb_mps * dt)
            if pos.alt <= 0.0:
                self._armed = False
            return
        if self._target is None:
            return
        d_alt = self._target.alt - pos.alt
        step_alt = self.climb_mps * dt
        pos.alt += max(-step_alt, min(step_alt, d_alt))
        if pos.alt < 1.0 and self._target.alt > 1.0:
            return
        dist = haversine_m(pos.lat, pos.lon, self._target.lat, self._target.lon)
        if dist < 0.05:
            return
        travel = min(dist, self.speed_mps * dt)
        fraction = travel / dist
        d_north = math.radians(self._target.lat - pos.lat) * 6371008.8 * fraction
        d_east = (math.radians(self._target.lon - pos.lon) * 6371008.8
                  * math.cos(math.radians(pos.lat)) * fraction)
        d_lat, d_lon = meters_to_degrees(pos.lat, d_north, d_east)
        pos.lat += d_lat
        pos.lon += d_lon

    def distance_to(self, lat: float, lon: float, alt: float) -> float:
        p = self._position
        return distance_3d_m(p.lat, p.lon, p.alt, lat, lon, alt)
