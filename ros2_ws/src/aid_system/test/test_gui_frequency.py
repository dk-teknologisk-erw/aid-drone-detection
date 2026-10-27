import json
import time
from unittest.mock import Mock

import pytest
import rclpy
from std_msgs.msg import Float32, String

from aid_system.control_node import SystemControlNode
from aid_system.operator_gui import OperatorGui


@pytest.fixture
def gui():
    gui = Mock(
        maximum_frequency=None, maximum_received_at=0.0, maximum_label="",
    )
    gui.decode = OperatorGui.decode
    gui.refresh_frequency = lambda: OperatorGui.refresh_frequency(gui)
    return gui


@pytest.mark.parametrize("mode", ["idle", "calibrate", "explore", "focus", "tracking"])
def test_gui_displays_maximum_even_without_viable_candidate(gui, mode):
    OperatorGui.detection_callback(gui, String(data=json.dumps({
        "mode": mode, "viable": False, "reason": "no_baseline", "band": "5.8ghz",
        "live_maximum": {"frequency_mhz": 5791.125, "amplitude_dbm": -62.5},
    })))
    assert gui.maximum_frequency == 5791.125
    assert "5791.125 MHz" in gui.maximum_text.set.call_args.args[0]
    gui.frequency_send_button.configure.assert_called_with(state="normal")
    gui.frequency_publisher.publish.assert_not_called()
    OperatorGui.send_frequency(gui)
    message = gui.frequency_publisher.publish.call_args.args[0]
    assert isinstance(message, Float32)
    assert message.data == pytest.approx(5791.125)


def test_gui_retains_but_marks_stale_frequency_and_refuses_send(gui):
    gui.maximum_frequency = 5780.0
    gui.maximum_received_at = time.monotonic() - 4
    gui.maximum_label = "Live maximum: 5780.000 MHz"
    OperatorGui.refresh_frequency(gui)
    assert "5780.000 MHz" in gui.maximum_text.set.call_args.args[0]
    assert "STALE" in gui.maximum_text.set.call_args.args[0]
    OperatorGui.send_frequency(gui)
    gui.frequency_publisher.publish.assert_not_called()


@pytest.mark.parametrize("maximum", [None, {}, {"frequency_mhz": float("nan")}])
def test_gui_cannot_send_invalid_or_missing_maximum(gui, maximum):
    OperatorGui.detection_callback(gui, String(data=json.dumps({
        "live_maximum": maximum,
    })))
    OperatorGui.send_frequency(gui)
    gui.frequency_publisher.publish.assert_not_called()
    gui.frequency_send_button.configure.assert_called_with(state="disabled")


def test_button_publishes_float32_over_ros_without_tracking_overwriting_it(gui):
    rclpy.init()
    node = rclpy.create_node("frequency_button_test")
    control = SystemControlNode()
    try:
        received = []
        node.create_subscription(Float32, "/aid/detection/freq", received.append, 10)
        gui.frequency_publisher = node.create_publisher(Float32, "/aid/detection/freq", 10)
        gui.maximum_frequency = 5791.125
        gui.maximum_received_at = time.monotonic()
        OperatorGui.send_frequency(gui)
        for _ in range(30):
            rclpy.spin_once(node, timeout_sec=0.05)
            if received:
                break
        assert len(received) == 1
        assert received[0].data == pytest.approx(5791.125)
        control.accepted_target = {
            "frequency_mhz": 5780.0, "band": "5.8ghz",
        }
        control.start_tracking()
        for _ in range(5):
            control.control_tick()
            rclpy.spin_once(node, timeout_sec=0.05)
        assert len(received) == 1
        assert node.count_publishers("/aid/detection/freq") == 1
    finally:
        control.destroy_node()
        node.destroy_node()
        rclpy.shutdown()
