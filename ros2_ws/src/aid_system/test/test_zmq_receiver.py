from concurrent.futures import ThreadPoolExecutor
import json
import socket
import struct
import time

import pytest
import zmq

from aid_system.follower_cues import request_cue, validate_follower_status
from aid_system.orientation_sender import send_orientation
from aid_system.zmq_receiver import TestReceiver, cue_reply, simulated_status


@pytest.fixture
def listener():
    reservations = [socket.socket() for _ in range(3)]
    try:
        for reservation in reservations:
            reservation.bind(("127.0.0.1", 0))
        ports = [str(reservation.getsockname()[1]) for reservation in reservations]
    finally:
        for reservation in reservations:
            reservation.close()
    receiver = TestReceiver()
    receiver.start("127.0.0.1", *ports)
    yield receiver, ports
    receiver.close()


def status():
    return simulated_status("55", "10", "20", "0", "0")


def collect(receiver, predicate):
    records = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        records.extend(receiver.receive(status()))
        if predicate(records):
            return records
        time.sleep(0.005)
    raise AssertionError(f"Receiver timed out: {records}")


def test_real_sender_status_position_clear_and_local_search_arrive(listener):
    receiver, ports = listener
    with ThreadPoolExecutor(max_workers=1) as executor:
        for port, message in (
            (ports[0], {"cmd": "status"}),
            (ports[0], {"lat": 55.001, "lon": 10, "alt_m": 30, "alt_ref": "rel"}),
            (ports[1], {"n": 100, "e": 0, "d": -30}),
            (ports[0], {"cmd": "clear"}),
        ):
            reply = executor.submit(request_cue, "127.0.0.1", port, message)
            records = collect(receiver, lambda records: bool(records))
            result = reply.result(timeout=2)
            assert result["test_only"] is True
            assert json.dumps(message) in records[0]
            if message.get("cmd") == "status":
                validate_follower_status(result)
                assert result["armed"] is False
            else:
                assert result["ok"] is True
                assert "no flight action" in result["warning"]


def test_binary_compass_from_actual_sender_is_decoded(listener):
    receiver, ports = listener
    send_orientation(f"tcp://127.0.0.1:{ports[2]}", -91.25)
    records = collect(receiver, lambda records: bool(records))
    assert "COMPASS: -91.250 deg" in records[0]


def test_invalid_json_rejected_and_rep_socket_keeps_working(listener):
    receiver, ports = listener
    with zmq.Context() as context:
        with context.socket(zmq.REQ) as sender:
            sender.setsockopt(zmq.RCVTIMEO, 2000)
            sender.connect(f"tcp://127.0.0.1:{ports[0]}")
            for frames in ([b"bad json"], [b"{}", b"extra"], [b'{"cmd":"status"}']):
                sender.send_multipart(frames)
                collect(receiver, lambda records: bool(records))
                reply = sender.recv_json()
                assert reply.get("status", False) == (len(frames) == 1 and frames[0] != b"bad json")
                if frames[0] == b"bad json" or len(frames) > 1:
                    assert reply["ok"] is False


def test_invalid_compass_is_visible_and_does_not_break_listener(listener):
    receiver, ports = listener
    with zmq.Context() as context:
        with context.socket(zmq.PUSH) as sender:
            sender.setsockopt(zmq.SNDTIMEO, 2000)
            sender.connect(f"tcp://127.0.0.1:{ports[2]}")
            for data in (b"bad", struct.pack("<f", float("nan")), struct.pack("<f", 45)):
                sender.send(data)
                records = collect(receiver, lambda records: bool(records))
                assert ("INVALID" in records[0]) == (data != struct.pack("<f", 45))


@pytest.mark.parametrize("kind,message", [
    ("position", {"n": 1, "e": 2, "d": -30}),
    ("search", {"lat": 55, "lon": 10, "alt_m": 30, "alt_ref": "rel"}),
    ("position", {"lat": float("nan"), "lon": 10, "alt_m": 30, "alt_ref": "rel"}),
    ("position", {"cmd": "fly"}),
])
def test_wrong_payload_and_nonfinite_values_rejected(kind, message):
    with pytest.raises(ValueError):
        cue_reply(message, kind, status())


def test_duplicate_ports_and_failed_bind_leave_no_partial_listeners(listener):
    receiver, ports = listener
    other = TestReceiver()
    try:
        with pytest.raises(ValueError, match="different"):
            other.start("127.0.0.1", ports[0], ports[0], ports[2])
        assert not other.sockets
        receiver.sockets.pop("compass").close(linger=0)
        with pytest.raises(zmq.ZMQError):
            other.start("127.0.0.1", ports[2], ports[1], ports[0])
        assert not other.sockets
        with other.context.socket(zmq.PULL) as probe:
            probe.bind(f"tcp://127.0.0.1:{ports[2]}")
    finally:
        other.close()


def test_stop_releases_ports_for_restart(listener):
    receiver, ports = listener
    receiver.stop()
    assert not receiver.sockets
    receiver.start("127.0.0.1", *ports)
    assert len(receiver.sockets) == 3


def test_blank_simulated_gps_is_explicitly_unavailable_not_an_error():
    result = simulated_status("", "", "0", "0", "0")
    assert result["status"] is True
    assert result["gps_ok"] is False
    assert result["lat"] is None and result["lon"] is None
    with pytest.raises(ValueError, match="GPS unavailable"):
        validate_follower_status(result)
    assert validate_follower_status(result, (55.3222, 10.3232))["pos_ok"] is True


@pytest.mark.parametrize("lat,lon", [("", "10"), ("55", ""), ("bad", "10"), ("91", "10")])
def test_partially_entered_or_invalid_simulated_gps_is_not_silently_ignored(lat, lon):
    with pytest.raises(ValueError):
        simulated_status(lat, lon, "0", "0", "0")
