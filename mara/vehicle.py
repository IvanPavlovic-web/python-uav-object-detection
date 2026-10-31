import time
from typing import Optional

from .logger import EventType, FlightLogger
from .simulated import SimLocation, SimMode, SimulatedVehicle


class VehicleError(RuntimeError):
    pass


def _apply_dronekit_compat() -> None:
    import collections
    import collections.abc
    for name in ("MutableMapping", "Mapping", "MutableSequence", "Iterable", "Callable"):
        if not hasattr(collections, name):
            setattr(collections, name, getattr(collections.abc, name))


def _import_dronekit():
    _apply_dronekit_compat()
    try:
        import dronekit
    except ImportError as exc:
        missing = getattr(exc, "name", None) or "dronekit"
        what = "Paket 'dronekit'" if missing == "dronekit" else f"Zavisnost '{missing}' za DroneKit"
        raise VehicleError(f"{what} nije instaliran. Pokrenite: "
                           "pip install -r requirements.txt") from exc
    except Exception as exc:
        raise VehicleError(f"DroneKit se ne može učitati ({exc}). Koristite "
                           "Python 3.9 ili 3.10, vidi README.") from exc
    return dronekit


def connect_vehicle(connection_string: str = "127.0.0.1:14550", baud: int = 57600,
                    heartbeat_timeout: int = 30, logger: Optional[FlightLogger] = None):
    dronekit = _import_dronekit()
    print(f"Povezivanje na: {connection_string}")
    try:
        vehicle = dronekit.connect(connection_string, baud=baud, wait_ready=True,
                                   heartbeat_timeout=heartbeat_timeout)
    except Exception as exc:
        raise VehicleError(f"Povezivanje nije uspjelo ({connection_string}): {exc}") from exc
    if logger:
        logger.log(EventType.CONNECTED, f"Veza uspostavljena: {connection_string}", vehicle)
    return vehicle


def create_simulated_vehicle(time_scale: float = 1.0, logger: Optional[FlightLogger] = None):
    vehicle = SimulatedVehicle(time_scale=time_scale)
    if logger:
        logger.log(EventType.CONNECTED, "Pokrenut ugrađeni simulator (bez ArduPilot SITL)", vehicle)
    return vehicle


def make_mode(name: str):
    try:
        return _import_dronekit().VehicleMode(name)
    except VehicleError:
        return SimMode(name)


def make_location(lat: float, lon: float, alt: float):
    try:
        return _import_dronekit().LocationGlobalRelative(lat, lon, alt)
    except VehicleError:
        return SimLocation(lat, lon, alt)


def get_position(vehicle):
    loc = vehicle.location.global_relative_frame
    return loc.lat, loc.lon, loc.alt


def set_mode(vehicle, mode_name: str, timeout: float = 10.0) -> None:
    vehicle.mode = make_mode(mode_name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vehicle.mode.name == mode_name:
            return
        time.sleep(0.1)
    raise VehicleError(f"Režim {mode_name} nije potvrđen u roku od {timeout:.0f} s.")


def _wait_until(condition, timeout: float, what: str, poll: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise VehicleError(f"Isteklo vrijeme čekanja: {what}")
        time.sleep(poll)


def arm_and_takeoff(vehicle, target_altitude: float, logger: Optional[FlightLogger] = None,
                    timeout: float = 180.0) -> None:
    log = (lambda t, m: logger.log(t, m, vehicle)) if logger else (lambda t, m: print(m))

    log(EventType.INFO, "Provjera spremnosti za armiranje (is_armable)...")
    _wait_until(lambda: vehicle.is_armable, timeout, "letjelica nije spremna za armiranje")

    set_mode(vehicle, "GUIDED")
    log(EventType.INFO, "Režim GUIDED postavljen, armiram motore.")

    vehicle.armed = True
    _wait_until(lambda: vehicle.armed, 30, "motori se nisu armirali")

    log(EventType.TAKEOFF_STARTED, f"Polijetanje na {target_altitude:.1f} m")
    vehicle.simple_takeoff(target_altitude)

    def reached() -> bool:
        return vehicle.location.global_relative_frame.alt >= target_altitude * 0.95

    _wait_until(reached, timeout, "ciljna visina nije dostignuta")
    log(EventType.TAKEOFF_COMPLETED,
        f"Dostignuta visina {vehicle.location.global_relative_frame.alt:.1f} m")
