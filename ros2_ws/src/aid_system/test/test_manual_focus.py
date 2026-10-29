import json
import time
from unittest.mock import Mock, patch

import pytest
import rclpy
from std_msgs.msg import String

from aid_system.control_node import SystemControlNode
from aid_system.operator_gui import OperatorGui


@pytest.fixture
def control():
    rclpy.init()
    node = SystemControlNode()
    node.motor_status_callback(String(data=json.dumps({
        "connected": True, "angle_deg": 359.0, "moving": False,
    })))
    yield node
    node.destroy_node()
    rclpy.shutdown()


@pytest.mark.parametrize("direction,expected", [("right", 2.0), ("left", 356.0)])
def test_jog_works_without_target_and_wraps_angle(control, direction, expected):
    with patch.object(control, "publish_tracking_target") as move:
        control.command_callback(String(data=json.dumps({
            "command": "jog", "action": "start", "direction": direction,
        })))
        move.assert_called_once_with(359.0 + (3.0 if direction == "right" else -3.0))
        assert move.call_args.args[0] % 360 == expected
    assert control.mode == "focus"
    assert control.accepted_target is None
    assert control.selected_candidate is None
    assert control.jog_direction == (1 if direction == "right" else -1)


def test_release_stops_and_keepalive_does_not_restart(control):
    control.set_focus_jog("start", "right")
    with patch.object(control.motor_mode_publisher, "publish") as publish:
        control.set_focus_jog("stop", "")
        assert publish.call_args.args[0].data == "stop"
    assert control.jog_direction == 0
    with pytest.raises(ValueError, match="expired"):
        control.set_focus_jog("keepalive", "right")
    assert control.jog_direction == 0


def test_keepalive_cannot_revive_expired_lease_before_timer_fires(control):
    control.set_focus_jog("start", "right")
    control.jog_expires_at = time.monotonic() - 0.1
    with pytest.raises(ValueError, match="expired"):
        control.set_focus_jog("keepalive", "right")
    assert control.jog_direction == 0


@pytest.mark.parametrize("expiry", ["lease", "telemetry"])
def test_missing_release_or_telemetry_stops_motion(control, expiry):
    control.set_focus_jog("start", "left")
    if expiry == "lease":
        control.jog_expires_at = time.monotonic() - 0.1
    else:
        control.motor_status_at = time.monotonic() - 1
    with patch.object(control.motor_mode_publisher, "publish") as publish:
        control.control_tick()
        assert publish.call_args.args[0].data == "stop"
    assert control.jog_direction == 0


def test_keepalive_renews_and_continues_using_latest_motor_position(control):
    control.set_focus_jog("start", "right")
    expires = control.jog_expires_at
    control.set_focus_jog("keepalive", "right")
    assert control.jog_expires_at >= expires
    control.motor_status_callback(String(data=json.dumps({
        "connected": True, "angle_deg": 1.0, "moving": True,
    })))
    control.jog_next_at = 0
    with patch.object(control, "publish_tracking_target") as move:
        control.control_tick()
        move.assert_called_once_with(4.0)


@pytest.mark.parametrize("mode", ["idle", "explore", "focus"])
def test_mode_switch_cancels_jog_and_late_keepalive_cannot_restart(control, mode):
    control.set_focus_jog("start", "right")
    control.set_mode(mode)
    assert control.jog_direction == 0
    with pytest.raises(ValueError):
        control.set_focus_jog("keepalive", "right")
    assert control.jog_direction == 0


def test_disconnect_stops_and_rejects_jog_until_motor_returns(control):
    control.set_focus_jog("start", "left")
    control.motor_status_callback(String(data='{"connected":false}'))
    assert control.jog_direction == 0
    with pytest.raises(ValueError, match="telemetry"):
        control.set_focus_jog("start", "left")


def test_jog_exits_tracking_and_cancels_pending_dual_focus(control):
    control.mode = "tracking"
    control.accepted_target = {"frequency_mhz": 5780}
    control.selected_candidate = {"antenna_angle_deg": 10}
    control.pending_focus = {"target": 10, "band": "2.4"}
    control.set_focus_jog("start", "left")
    assert control.mode == "focus"
    assert control.accepted_target is None
    assert control.selected_candidate is None
    assert control.pending_focus is None
    assert control.dual_scheduler.phase == "idle"


@pytest.fixture
def gui():
    gui = Mock(
        jog_direction=None, jog_after_id=None, cue_motor_at=time.monotonic(),
        cue_motor={"connected": True},
    )
    gui.stop_focus_jog = lambda: OperatorGui.stop_focus_jog(gui)
    gui.send = lambda **fields: OperatorGui.send(gui, **fields)
    return gui


def messages(gui):
    return [
        json.loads(call.args[0].data)
        for call in gui.control_publisher.publish.call_args_list
    ]


def test_gui_press_renews_and_release_cancels_timer(gui):
    OperatorGui.start_focus_jog(gui, "left")
    assert gui.jog_direction == "left"
    assert messages(gui)[-1] == {"command": "jog", "action": "start", "direction": "left"}
    assert gui.root.after.call_args.args[0] == 100
    OperatorGui.renew_focus_jog(gui)
    assert messages(gui)[-1]["action"] == "keepalive"
    OperatorGui.stop_focus_jog(gui)
    gui.root.after_cancel.assert_called()
    assert gui.jog_direction is None
    assert messages(gui)[-1] == {"command": "jog", "action": "stop"}
    count = len(messages(gui))
    OperatorGui.renew_focus_jog(gui)
    assert len(messages(gui)) == count


def test_other_gui_command_stops_jog_before_sending(gui):
    OperatorGui.start_focus_jog(gui, "right")
    OperatorGui.send(gui, command="mode", mode="explore")
    assert messages(gui)[-2:] == [
        {"command": "jog", "action": "stop"},
        {"command": "mode", "mode": "explore"},
    ]


def test_stale_gui_telemetry_prevents_start_and_stops_renewal(gui):
    gui.cue_motor_at = time.monotonic() - 1
    OperatorGui.start_focus_jog(gui, "right")
    assert not messages(gui)
    gui.cue_motor_at = time.monotonic()
    OperatorGui.start_focus_jog(gui, "right")
    gui.cue_motor_at -= 1
    OperatorGui.renew_focus_jog(gui)
    assert gui.jog_direction is None
    assert messages(gui)[-1]["action"] == "stop"
