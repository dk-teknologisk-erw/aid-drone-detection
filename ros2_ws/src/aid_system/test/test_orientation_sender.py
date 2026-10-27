from concurrent.futures import Future
import struct
from unittest.mock import Mock

import pytest
from std_msgs.msg import String
import zmq

from aid_system.operator_gui import OperatorGui
from aid_system.orientation_sender import orientation_endpoint, send_orientation


@pytest.mark.parametrize(
    ("address", "port", "expected"),
    [
        ("192.168.1.20", "5555", "tcp://192.168.1.20:5555"),
        (" 127.0.0.1 ", " 65535 ", "tcp://127.0.0.1:65535"),
        ("::1", "1", "tcp://[::1]:1"),
    ],
)
def test_orientation_endpoint(address, port, expected):
    assert orientation_endpoint(address, port) == expected


@pytest.mark.parametrize("address", ["", "receiver", "192.168.1.999"])
def test_orientation_endpoint_rejects_invalid_ip(address):
    with pytest.raises(ValueError, match="valid IP address"):
        orientation_endpoint(address, "5555")


@pytest.mark.parametrize("port", ["", "0", "-1", "65536", "5555.5", "abc"])
def test_orientation_endpoint_rejects_invalid_port(port):
    with pytest.raises(ValueError, match="Port must be an integer"):
        orientation_endpoint("127.0.0.1", port)


@pytest.mark.parametrize("heading", [0.0, -91.25, 359.125])
def test_send_orientation_transmits_one_little_endian_float32(heading):
    with zmq.Context() as context:
        with context.socket(zmq.PULL) as receiver:
            receiver.setsockopt(zmq.RCVTIMEO, 2000)
            port = receiver.bind_to_random_port("tcp://127.0.0.1")

            send_orientation(f"tcp://127.0.0.1:{port}", heading)

            frames = receiver.recv_multipart()
            assert len(frames) == 1
            assert len(frames[0]) == 4
            assert struct.unpack("<f", frames[0])[0] == pytest.approx(heading)


def test_send_orientation_times_out_without_a_receiver():
    with zmq.Context() as context:
        with context.socket(zmq.PULL) as receiver:
            port = receiver.bind_to_random_port("tcp://127.0.0.1")
            endpoint = f"tcp://127.0.0.1:{port}"
            receiver.unbind(endpoint)

            with pytest.raises(zmq.Again):
                send_orientation(endpoint, 42.0, timeout_ms=50)


@pytest.mark.parametrize("heading", [float("nan"), float("inf"), -float("inf")])
def test_send_orientation_rejects_nonfinite_heading(heading):
    with pytest.raises(ValueError, match="must be finite"):
        send_orientation("tcp://127.0.0.1:5555", heading)


@pytest.fixture
def gui():
    gui = Mock(compass_heading=-91.25, orientation_future=None, orientation_after_id=None)
    gui.orientation_ip.get.return_value = "192.168.1.20"
    gui.orientation_port.get.return_value = "5555"
    return gui


@pytest.mark.parametrize(
    ("address", "port", "heading", "pending", "expected"),
    [
        ("192.168.1.20", "5555", -91.25, False, "normal"),
        ("192.168.1.20", "5555", 0.0, False, "normal"),
        ("", "5555", -91.25, False, "disabled"),
        ("192.168.1.20", "", -91.25, False, "disabled"),
        ("192.168.1.20", "65536", -91.25, False, "disabled"),
        ("192.168.1.20", "5555", None, False, "disabled"),
        ("192.168.1.20", "5555", -91.25, True, "disabled"),
    ],
)
def test_gui_requires_destination_and_heading(gui, address, port, heading, pending, expected):
    gui.orientation_ip.get.return_value = address
    gui.orientation_port.get.return_value = port
    gui.compass_heading = heading
    gui.orientation_future = Future() if pending else None

    OperatorGui.update_orientation_send_button(gui)

    gui.orientation_send_button.configure.assert_called_once_with(state=expected)


def test_gui_sends_snapshot_of_current_signed_compass_heading(gui):
    future = Future()
    gui.orientation_executor.submit.return_value = future

    OperatorGui.send_compass_orientation(gui)

    gui.orientation_executor.submit.assert_called_once_with(
        send_orientation, "tcp://192.168.1.20:5555", -91.25
    )
    assert gui.orientation_future is future
    gui.root.after.assert_called_once()
    assert gui.root.after.call_args.args[0] == 50


@pytest.mark.parametrize("heading", [None, float("nan"), float("inf")])
def test_gui_does_not_send_missing_or_nonfinite_compass_readings(gui, heading):
    gui.decode.return_value = {"signed_heading_deg": heading}

    OperatorGui.compass_callback(gui, String())
    OperatorGui.send_compass_orientation(gui)

    assert gui.compass_heading is None
    gui.orientation_executor.submit.assert_not_called()
    gui.orientation_status.set.assert_called_with("Waiting for compass orientation")


def test_gui_reports_queued_message_without_claiming_delivery(gui):
    gui.orientation_future = Future()
    gui.orientation_future.set_result(None)

    OperatorGui.check_orientation_send(gui, "tcp://192.168.1.20:5555", -91.25)

    gui.orientation_status.set.assert_called_once_with(
        "Queued -91.2 deg to tcp://192.168.1.20:5555"
    )
    assert gui.orientation_future is None
    gui.update_orientation_send_button.assert_called_once()


def test_gui_reports_unavailable_receiver_and_allows_retry(gui):
    gui.orientation_future = Future()
    gui.orientation_future.set_exception(zmq.Again())

    OperatorGui.check_orientation_send(gui, "tcp://192.168.1.20:5555", -91.25)

    gui.orientation_status.set.assert_called_once_with(
        "Receiver unavailable: connection timed out"
    )
    assert gui.orientation_future is None
    gui.update_orientation_send_button.assert_called_once()


def test_gui_polls_pending_send_without_blocking(gui):
    gui.orientation_future = Future()

    OperatorGui.check_orientation_send(gui, "tcp://192.168.1.20:5555", -91.25)

    assert not gui.orientation_future.done()
    gui.root.after.assert_called_once()
    assert gui.root.after.call_args.args[0] == 50


def test_gui_close_cancels_poll_and_shuts_down_sender(gui):
    gui.orientation_after_id = "after#1"

    OperatorGui.close(gui)

    gui.root.after_cancel.assert_called_once_with("after#1")
    gui.orientation_executor.shutdown.assert_called_once_with(
        wait=False, cancel_futures=True
    )
    gui.root.quit.assert_called_once()