import hashlib
import hmac
import json
import math
import threading
import time

import pytest
import zmq

from aid_system.follower_cues import (
    EARTH_RADIUS_M, estimated_position, horizontal_distance, request_cue,
    sign_message, validate_cue_against_status, validate_follower_status,
)


def follower_status():
    return {
        "status": True, "gps_ok": True, "pos_ok": True,
        "lat": 55.0, "lon": 10.0, "alt_rel_m": 20.0, "pos_ned": [0.0, 0.0, -20.0],
        "state": "search", "mode": "OFFBOARD", "armed": True,
    }


@pytest.mark.parametrize("heading", [0.0, 90.0, 180.0, 270.0, -90.0, 359.0])
def test_destination_distance_and_direction(heading):
    cue = estimated_position(55, 10, 100, 30, heading, 0)
    assert horizontal_distance(55, 10, cue["lat"], cue["lon"]) == pytest.approx(100, abs=0.001)
    north = math.radians(cue["lat"] - 55) * EARTH_RADIUS_M
    east = math.radians(cue["lon"] - 10) * EARTH_RADIUS_M * math.cos(math.radians(55))
    assert north == pytest.approx(100 * math.cos(math.radians(heading)), abs=0.01)
    assert east == pytest.approx(100 * math.sin(math.radians(heading)), abs=0.01)
    assert cue["alt_m"] == 30
    assert cue["alt_ref"] == "rel"
    assert set(cue) == {"lat", "lon", "alt_m", "alt_ref"}


def test_declination_correction_and_date_line():
    corrected = estimated_position(0, 0, 100, 30, 80, 10)
    assert corrected["lat"] == pytest.approx(0, abs=1e-8)
    cue = estimated_position(0, 179.9999, 100, 30, 90, 0)
    assert -180 <= cue["lon"] < -179.99
    assert horizontal_distance(0, 179.9999, cue["lat"], cue["lon"]) == pytest.approx(100)


@pytest.mark.parametrize("index,value", [
    (0, 91), (1, 181), (2, 0), (2, -1), (2, 401), (3, 4), (3, 121),
    (4, float("nan")), (5, 181),
    *[(index, value) for index in range(6) for value in (float("inf"), "", None, True)],
])
def test_invalid_coordinates_and_parameters(index, value):
    arguments = [55, 10, 100, 30, 90, 0]
    arguments[index] = value
    with pytest.raises(ValueError):
        estimated_position(*arguments)


def test_status_limits_use_reported_position_without_assumption():
    cue = estimated_position(55, 10, 100, 30, 90, 0)
    status = follower_status()
    assert validate_cue_against_status(cue, status) == pytest.approx(100)
    status["lon"] = cue["lon"]
    assert validate_cue_against_status(cue, status) < 0.01
    status["lat"] = 56
    with pytest.raises(ValueError, match="limit"):
        validate_cue_against_status(cue, status)


def test_explicit_assumption_reuses_tower_coordinates_without_claiming_gps_fix():
    cue = estimated_position(55, 10, 100, 30, 90, 0)
    status = {**follower_status(), "gps_ok": False, "lat": None, "lon": None}
    assumed = (55.0, 10.0)
    assert validate_cue_against_status(cue, status, assumed) == pytest.approx(100)
    assert validate_follower_status(status, assumed) == status
    assert status["lat"] is None and not status["gps_ok"]
    with pytest.raises(ValueError, match="GPS unavailable"):
        validate_cue_against_status(cue, status)


@pytest.mark.parametrize("change", [
    {"status": False}, {"pos_ok": False}, {"pos_ned": [0, 0]}, {"alt_rel_m": ""},
])
def test_assumption_does_not_bypass_other_invalid_follower_status(change):
    with pytest.raises(ValueError):
        validate_follower_status({**follower_status(), **change}, (55, 10))


@pytest.mark.parametrize("change", [
    {"status": False}, {"gps_ok": False}, {"pos_ok": False},
    {"lat": float("nan")}, {"pos_ned": [0, 0]}, {"alt_rel_m": None},
])
def test_malformed_or_unusable_status(change):
    with pytest.raises(ValueError):
        validate_follower_status({**follower_status(), **change})


def test_signature_compatible_with_supplied_canonical_json():
    original = {"lat": 55.0, "lon": 10.0, "alt_m": 30.0, "alt_ref": "rel"}
    signed = sign_message(original, "test-secret")
    body = json.dumps(
        {key: value for key, value in signed.items() if key != "sig"},
        sort_keys=True, separators=(",", ":"),
    )
    expected = hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()
    assert signed["sig"] == expected
    assert "sig" not in original
    assert sign_message(original, "test-secret")["seq"] > signed["seq"]
    assert sign_message({"cmd": "status"}, "test-secret") == {"cmd": "status"}
    assert sign_message(original, "") == original


def test_request_round_trip_with_simulated_follower():
    received = []
    with zmq.Context() as context:
        ready = threading.Event()
        ports = []

        def follower():
            with context.socket(zmq.REP) as receiver:
                receiver.setsockopt(zmq.RCVTIMEO, 2000)
                ports.append(receiver.bind_to_random_port("tcp://127.0.0.1"))
                ready.set()
                for _ in range(3):
                    message = receiver.recv_json()
                    received.append(message)
                    receiver.send_json(follower_status() if message.get("cmd") == "status"
                                       else {"ok": True})

        worker = threading.Thread(target=follower)
        worker.start()
        assert ready.wait(2)
        try:
            assert request_cue("127.0.0.1", str(ports[0]), {"cmd": "status"})["gps_ok"]
            cue = estimated_position(55, 10, 100, 30, 90, 0)
            assert request_cue("127.0.0.1", str(ports[0]), cue, "secret") == {"ok": True}
            assert request_cue("127.0.0.1", str(ports[0]), {"cmd": "clear"}) == {"ok": True}
        finally:
            worker.join(timeout=3)
        assert not worker.is_alive()
    assert received[1]["alt_ref"] == "rel"
    assert "sig" in received[1]
    assert received[2] == {"cmd": "clear"}


def test_unavailable_follower_times_out_without_hanging():
    with zmq.Context() as context:
        with context.socket(zmq.REP) as receiver:
            port = receiver.bind_to_random_port("tcp://127.0.0.1")
            receiver.unbind(f"tcp://127.0.0.1:{port}")
            started = time.monotonic()
            with pytest.raises(TimeoutError, match="outcome unknown"):
                request_cue("127.0.0.1", str(port), {"cmd": "status"}, timeout_ms=50)
            assert time.monotonic() - started < 1


@pytest.mark.parametrize("reply", [b"invalid JSON", b"[]", None])
def test_invalid_or_missing_acknowledgement_is_unknown_outcome(reply):
    with zmq.Context() as context:
        ready = threading.Event()
        ports = []

        def follower():
            with context.socket(zmq.REP) as receiver:
                receiver.setsockopt(zmq.RCVTIMEO, 1000)
                receiver.setsockopt(zmq.LINGER, 0)
                ports.append(receiver.bind_to_random_port("tcp://127.0.0.1"))
                ready.set()
                receiver.recv()
                if reply is not None:
                    receiver.send(reply)
                else:
                    threading.Event().wait(0.2)

        worker = threading.Thread(target=follower)
        worker.start()
        assert ready.wait(2)
        try:
            error = TimeoutError if reply is None else ValueError
            with pytest.raises(error, match="outcome unknown"):
                request_cue(
                    "127.0.0.1", str(ports[0]), {"cmd": "clear"}, timeout_ms=100,
                )
        finally:
            worker.join(timeout=2)
        assert not worker.is_alive()
