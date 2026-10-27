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
    control_node.best_candidate_expires_at = time.monotonic() + 45
    control_node.rf_ready_band = "2.4"
    control_node.accepted_target = candidate(90)

    control_node.focus_best_candidate()

    assert control_node.mode == "focus"
    assert control_node.best_candidate is None
    assert control_node.selected_candidate is not None
    assert control_node.accepted_target is None
    with pytest.raises(ValueError, match="no accepted target"):
        control_node.start_tracking()
    control_node.motor_status_callback(
        String(
            data=json.dumps(
                {"connected": True, "moving": False, "angle_deg": 42.0}
            )
        )
    )
    assert control_node.focus_arrived
    assert not control_node.confirmation_available()
    with pytest.raises(ValueError, match="live signal"):
        control_node.accept_selected_candidate()
    control_node.detection_callback(String(data=json.dumps({
        **candidate(), "mode": "focus",
    })))
    assert control_node.confirmation_available()

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
    assert control_node.mode == "explore"
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


def test_expired_candidate_cannot_be_focused(control_node):
    control_node.mode = "explore"
    control_node.best_candidate = candidate()
    control_node.best_candidate_expires_at = time.monotonic() - 1
    control_node.focus_best_candidate()
    assert control_node.mode == "explore"
    assert control_node.selected_candidate is None


def test_discard_can_cancel_focus_before_arrival(control_node):
    control_node.mode = "focus"
    control_node.selected_candidate = candidate()
    control_node.discard_candidate()
    assert control_node.mode == "explore"
    assert control_node.selected_candidate is None
    assert control_node.accepted_target is None


def test_focus_confirmation_expires_and_wrong_frequency_cannot_refresh_it(control_node):
    control_node.mode = "focus"
    control_node.selected_candidate = candidate()
    control_node.focus_arrived = True
    control_node.focus_signal_at = time.monotonic() - 4
    control_node.detection_callback(String(data=json.dumps({
        **candidate(), "frequency_mhz": 2460, "mode": "focus",
    })))
    assert not control_node.confirmation_available()


def test_tracking_recovers_after_two_matching_samples(control_node):
    control_node.accepted_target = candidate()
    control_node.start_tracking()
    control_node.last_target_seen_at = time.monotonic() - 4
    control_node.control_tick()
    assert control_node.target_lost
    message = String(data=json.dumps({
        **candidate(), "mode": "tracking",
        "frequency_mhz": 2446.0, "amplitude_dbm": -65.0,
    }))
    control_node.detection_callback(message)
    assert control_node.target_lost
    control_node.detection_callback(message)
    assert not control_node.target_lost
    assert control_node.accepted_target["current_frequency_mhz"] == 2446
    assert control_node.accepted_target["frequency_mhz"] == 2440


@pytest.mark.parametrize("change", [
    {"mode": "explore"}, {"band": "5.8ghz"},
    {"frequency_mhz": 2470.0}, {"viable": False},
])
def test_unrelated_detection_cannot_keep_tracking_alive(control_node, change):
    control_node.accepted_target = candidate()
    control_node.start_tracking()
    seen_at = control_node.last_target_seen_at
    control_node.detection_callback(String(data=json.dumps({
        **candidate(), "mode": "tracking", "amplitude_dbm": -65.0, **change,
    })))
    assert control_node.last_target_seen_at == seen_at


def test_stop_clears_focused_selection(control_node):
    control_node.selected_candidate = candidate()
    control_node.set_mode("idle")
    assert control_node.selected_candidate is None
    assert not control_node.confirmation_available()