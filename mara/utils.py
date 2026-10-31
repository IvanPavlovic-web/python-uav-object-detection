import math
import os
from datetime import datetime, timezone
from typing import Tuple, Union

EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def distance_3d_m(lat1: float, lon1: float, alt1: float,
                  lat2: float, lon2: float, alt2: float) -> float:
    horizontal = haversine_m(lat1, lon1, lat2, lon2)
    return math.hypot(horizontal, alt2 - alt1)


def meters_to_degrees(lat: float, d_north_m: float, d_east_m: float) -> Tuple[float, float]:
    d_lat = math.degrees(d_north_m / EARTH_RADIUS_M)
    d_lon = math.degrees(d_east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return d_lat, d_lon


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def parse_camera_source(value: Union[str, int]) -> Union[str, int]:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def parse_host_port(text: str, default_port: int = 8765) -> Tuple[str, int]:
    text = text.strip()
    if not text:
        raise ValueError("Adresa ne smije biti prazna.")
    if ":" in text:
        host, _, port_text = text.rpartition(":")
        if not port_text.isdigit():
            raise ValueError(f"Neispravan port u adresi: {text!r}")
        port = int(port_text)
    else:
        host, port = text, default_port
    if not 0 < port < 65536:
        raise ValueError(f"Port van opsega: {port}")
    return host, port
