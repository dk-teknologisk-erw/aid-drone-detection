import json
import time

import pytest
import rclpy
from std_msgs.msg import String

from aid_system.control_node import SystemControlNode


@pytest.fixture
def control_node():
    rclpy.init()
    node = SystemControlNode()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def candidate(angle: float = 42.0) -> dict:
    return {
        "viable": True,
        "band": "2.4ghz",
        "frequency_mhz": 2440.0,
        "antenna_angle_deg": angle,
        "z_score": 8.0,
    }


def test_focus_consumes_candidate_and_accept_starts_tracking(control_node):
    control_node.mode = "explore"
    control_node.best_candidate = candidate()

    control_node.focus_best_candidate()

    assert control_node.mode == "focus"
    assert control_node.best_candidate is None
    assert control_node.selected_candidate is not None
    control_node.motor_status_callback(
        String(
            data=json.dumps(
                {"connected": True, "moving": False, "angle_deg": 42.0}
            )
        )
    )
    assert control_node.focus_arrived

    control_node.accept_selected_candidate()

    assert control_node.mode == "tracking"
    assert control_node.selected_candidate is None
    assert control_node.accepted_target is not None
    assert control_node.accepted_target["frequency_mhz"] == 2440.0


def test_decline_returns_to_explore_and_discard_clears_tracking(control_node):
    control_node.selected_candidate = candidate()
    control_node.mode = "focus"
    control_node.focus_arrived = True
    control_node.decline_selected_candidate()
    assert control_node.mode == "explore"
    assert control_node.selected_candidate is None

    control_node.accepted_target = candidate()
    control_node.start_tracking()
    control_node.discard_candidate()
    assert control_node.mode == "idle"
    assert control_node.accepted_target is None


def test_tracking_holds_after_signal_timeout(control_node):
    control_node.accepted_target = candidate()
    control_node.start_tracking()
    control_node.last_target_seen_at = (
        time.monotonic() - control_node.tracking_signal_timeout
    )

    control_node.control_tick()

    assert control_node.target_lost
    assert control_node.manual_direction == 0


def test_manual_motion_has_priority_after_signal_timeout(control_node):
    control_node.accepted_target = candidate()
    control_node.start_tracking()
    control_node.target_lost = True
    control_node.last_target_seen_at = 0.0
    control_node.set_manual_motion("start", "clockwise")

    control_node.control_tick()

    assert control_node.manual_direction == 1