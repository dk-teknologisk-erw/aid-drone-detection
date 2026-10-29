import hashlib
import hmac
import json
import math
import threading
import time

import zmq

from .orientation_sender import orientation_endpoint


EARTH_RADIUS_M = 6371000.0
MAX_DISTANCE_M = 400.0
MIN_ALTITUDE_M = 5.0
MAX_ALTITUDE_M = 120.0
_sequence_lock = threading.Lock()
_last_sequence = 0


def finite_number(value: object, name: str) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def coordinates(latitude: object, longitude: object) -> tuple[float, float]:
    lat = finite_number(latitude, "Latitude")
    lon = finite_number(longitude, "Longitude")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError("Latitude/longitude out of range")
    return lat, lon


def estimated_position(
    latitude: object,
    longitude: object,
    distance_m: object,
    altitude_m: object,
    magnetic_bearing_deg: object,
    correction_deg: object,
) -> dict:
    lat, lon = coordinates(latitude, longitude)
    distance = finite_number(distance_m, "Horizontal distance")
    altitude = finite_number(altitude_m, "Altitude above follower home")
    heading = finite_number(magnetic_bearing_deg, "Compass bearing")
    correction = finite_number(correction_deg, "Heading correction")
    if not 0.0 < distance <= MAX_DISTANCE_M:
        raise ValueError(f"Horizontal distance must be > 0 and <= {MAX_DISTANCE_M:g} m")
    if not MIN_ALTITUDE_M <= altitude <= MAX_ALTITUDE_M:
        raise ValueError(
            f"Altitude must be {MIN_ALTITUDE_M:g}-{MAX_ALTITUDE_M:g} m above follower home"
        )
    if not -180.0 <= correction <= 180.0:
        raise ValueError("Heading correction must be between -180 and 180 degrees")
    bearing = math.radians((heading + correction) % 360.0)
    angular_distance = distance / EARTH_RADIUS_M
    lat0, lon0 = math.radians(lat), math.radians(lon)
    lat1 = math.asin(max(-1.0, min(1.0,
        math.sin(lat0) * math.cos(angular_distance)
        + math.cos(lat0) * math.sin(angular_distance) * math.cos(bearing)
    )))
    lon1 = lon0 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat0),
        math.cos(angular_distance) - math.sin(lat0) * math.sin(lat1),
    )
    return {
        "lat": math.degrees(lat1),
        "lon": (math.degrees(lon1) + 180.0) % 360.0 - 180.0,
        "alt_m": altitude,
        "alt_ref": "rel",
    }


def horizontal_distance(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    phi0, phi1 = math.radians(lat0), math.radians(lat1)
    a = (
        math.sin((phi1 - phi0) / 2.0) ** 2
        + math.cos(phi0) * math.cos(phi1)
        * math.sin(math.radians(lon1 - lon0) / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def follower_coordinates(
    reply: dict, assumed_position: tuple[float, float] | None = None,
) -> tuple[float, float]:
    if assumed_position is not None:
        return coordinates(*assumed_position)
    if reply.get("gps_ok") is not True:
        raise ValueError("Follower GPS unavailable; provide GPS or explicitly assume follower at tower")
    return coordinates(reply.get("lat"), reply.get("lon"))


def validate_follower_status(
    reply: dict, assumed_position: tuple[float, float] | None = None,
) -> dict:
    if reply.get("status") is not True:
        raise ValueError(f"Follower status rejected: {reply.get('error', 'invalid reply')}")
    if reply.get("pos_ok") is not True:
        raise ValueError("Follower needs valid local position")
    follower_coordinates(reply, assumed_position)
    finite_number(reply.get("alt_rel_m"), "Follower altitude")
    position = reply.get("pos_ned")
    if not isinstance(position, list) or len(position) != 3:
        raise ValueError("Follower status must include three pos_ned coordinates")
    for value in position:
        finite_number(value, "Follower NED position")
    return reply.copy()


def validate_cue_against_status(
    cue: dict, status: dict, assumed_position: tuple[float, float] | None = None,
) -> float:
    validate_follower_status(status, assumed_position)
    lat, lon = follower_coordinates(status, assumed_position)
    distance = horizontal_distance(
        lat, lon, cue["lat"], cue["lon"],
    )
    if distance > MAX_DISTANCE_M:
        raise ValueError(f"Estimated point is {distance:.0f} m from follower; limit is 400 m")
    return distance


def sign_message(message: dict, secret: str) -> dict:
    global _last_sequence
    signed = message.copy()
    if secret and signed.get("cmd") != "status":
        with _sequence_lock:
            _last_sequence = max(_last_sequence + 1, time.time_ns() // 1_000_000)
            signed["seq"] = _last_sequence
        signed["t"] = round(time.time(), 3)
        body = json.dumps(signed, sort_keys=True, separators=(",", ":"), allow_nan=False)
        signed["sig"] = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256,
        ).hexdigest()
    return signed


def request_cue(
    host: str, port: str, message: dict, secret: str = "", timeout_ms: int = 3000,
) -> dict:
    endpoint = orientation_endpoint(host, port)
    if timeout_ms <= 0:
        raise ValueError("Request timeout must be positive")
    payload = json.dumps(sign_message(message, secret), allow_nan=False)
    deadline = time.monotonic() + timeout_ms / 1000.0
    with zmq.Context() as context:
        with context.socket(zmq.REQ) as sender:
            sender.setsockopt(zmq.IPV6, 1)
            sender.setsockopt(zmq.LINGER, 0)
            sender.setsockopt(zmq.IMMEDIATE, 1)
            sender.connect(endpoint)
            for event in (zmq.POLLOUT, zmq.POLLIN):
                remaining = max(0, math.ceil((deadline - time.monotonic()) * 1000))
                if not sender.poll(remaining, event):
                    raise TimeoutError(
                        "No acknowledgement; outcome unknown. Check follower before retrying."
                    )
                if event == zmq.POLLOUT:
                    sender.send_string(payload, flags=zmq.NOBLOCK)
            try:
                reply = json.loads(sender.recv_string(flags=zmq.NOBLOCK))
            except ValueError as error:
                raise ValueError(
                    "Invalid follower JSON reply; outcome unknown. Check follower."
                ) from error
    if not isinstance(reply, dict):
        raise ValueError("Follower reply must be a JSON object; outcome unknown")
    return reply
