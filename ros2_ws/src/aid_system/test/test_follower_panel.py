from concurrent.futures import Future
import time
from unittest.mock import Mock, patch

import pytest

from aid_system.follower_panel import FollowerPanel
from aid_system.operator_gui import OperatorGui


@pytest.fixture
def panel():
    panel = Mock()
    panel.future = None
    panel.closed = False
    panel.host.get.return_value = "127.0.0.1"
    panel.port.get.return_value = "5558"
    panel.secret.get.return_value = ""
    panel.latitude.get.return_value = "55"
    panel.longitude.get.return_value = "10"
    panel.distance.get.return_value = "100"
    panel.altitude.get.return_value = "30"
    panel.correction.get.return_value = "0"
    panel.follower_at_tower.get.return_value = True
    panel.context.return_value = 90.0
    panel.status = {
        "status": True, "gps_ok": True, "pos_ok": True, "lat": 55, "lon": 10,
        "alt_rel_m": 20, "pos_ned": [0, 0, -20],
    }
    panel.status_at = time.monotonic()
    panel.request_identity = ("127.0.0.1", "5558", "")
    panel.input_revision = 0
    panel.request_revision = 0
    panel.buttons = [Mock(), Mock(), Mock()]
    panel.identity = lambda: FollowerPanel.identity(panel)
    panel.assumed_position = lambda: FollowerPanel.assumed_position(panel)
    panel.require_status = lambda: FollowerPanel.require_status(panel)
    return panel


def test_send_requires_preview_confirmation_and_sends_snapshot(panel):
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True) as confirm:
        FollowerPanel.send_position(panel)
    confirm.assert_called_once()
    assert "not measured target GPS" in confirm.call_args.args[1]
    assert "ABOVE FOLLOWER HOME" in confirm.call_args.args[1]
    assert "ASSUMED AT TOWER" in confirm.call_args.args[1]
    cue = panel.start_request.call_args.args[0]
    assert cue["alt_ref"] == "rel"
    assert cue["alt_m"] == 30
    assert cue["lon"] > 10
    assert cue["lat"] == pytest.approx(55, abs=1e-6)


def test_cancel_does_not_send(panel):
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=False):
        FollowerPanel.send_position(panel)
    panel.start_request.assert_not_called()


@pytest.mark.parametrize("reason", ["stale", "destination", "moving"])
def test_send_rejects_missing_preconditions(panel, reason):
    if reason == "stale":
        panel.status_at = time.monotonic() - 11
    elif reason == "destination":
        panel.host.get.return_value = "127.0.0.2"
    else:
        panel.context.side_effect = ValueError("Antenna moving")
    with patch("aid_system.follower_panel.messagebox.askyesno") as confirm:
        FollowerPanel.send_position(panel)
    panel.start_request.assert_not_called()
    confirm.assert_not_called()


def test_uncorrected_compass_is_allowed_without_verification_acknowledgement(panel):
    panel.alignment_verified.get.return_value = False
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True) as confirm:
        FollowerPanel.send_position(panel)
    panel.start_request.assert_called_once()
    text = confirm.call_args.args[1]
    assert "compass 90.0 deg + correction +0.0 deg" in text
    assert "magnetic north" in text
    assert "True bearing" not in text


def test_changed_bearing_or_expired_status_during_confirmation_is_not_sent(panel):
    panel.context.side_effect = [90, 100]
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True):
        FollowerPanel.send_position(panel)
    panel.start_request.assert_not_called()
    assert "heading changed" in panel.status_text.set.call_args.args[0]


def test_clear_is_explicit_and_not_a_flight_stop(panel):
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True) as confirm:
        FollowerPanel.clear(panel)
    assert "NOT a flight stop" in confirm.call_args.args[1]
    panel.start_request.assert_called_once_with({"cmd": "clear"})


@pytest.mark.parametrize("reply,expected", [
    ({"ok": True}, "ACCEPTED"), ({"ok": False, "error": "locked"}, "REJECTED: locked"),
    ({}, "outcome unknown"),
])
def test_acknowledgements_are_not_misreported(panel, reply, expected):
    panel.future = Future()
    panel.future.set_result(reply)
    FollowerPanel.poll(panel, {"cmd": "clear"})
    assert expected in panel.status_text.set.call_args.args[0]
    assert panel.future is None


def test_timed_out_command_invalidates_status_and_requires_recheck(panel):
    panel.future = Future()
    panel.future.set_exception(TimeoutError("outcome unknown"))
    FollowerPanel.poll(panel, {"cmd": "clear"})
    assert panel.status is None
    assert "outcome unknown" in panel.status_text.set.call_args.args[0]


def test_reply_for_changed_destination_is_not_applied(panel):
    panel.future = Future()
    panel.future.set_result(panel.status)
    panel.host.get.return_value = "127.0.0.2"
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert panel.status is None
    assert "Destination changed" in panel.status_text.set.call_args.args[0]


def test_missing_receiver_gps_uses_tower_only_when_explicitly_checked(panel):
    reply = {**panel.status, "gps_ok": False, "lat": None, "lon": None}
    panel.future = Future()
    panel.future.set_result(reply)
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert panel.status["gps_ok"] is False
    assert panel.status["lat"] is None
    assert "ASSUMED AT TOWER" in panel.status_text.set.call_args.args[0]
    assert "55.000000, 10.000000" in panel.status_text.set.call_args.args[0]
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True):
        FollowerPanel.send_position(panel)
    panel.start_request.assert_called_once()

    panel.follower_at_tower.get.return_value = False
    panel.future = Future()
    panel.future.set_result(reply)
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert panel.status is None
    assert "GPS unavailable" in panel.status_text.set.call_args.args[0]


def test_unchecked_option_displays_and_uses_reported_gps(panel):
    panel.follower_at_tower.get.return_value = False
    reply = {**panel.status, "lat": 55.0001, "lon": 10.0001}
    panel.future = Future()
    panel.future.set_result(reply)
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert "Reported GPS 55.000100, 10.000100" in panel.status_text.set.call_args.args[0]
    with patch("aid_system.follower_panel.messagebox.askyesno", return_value=True) as confirm:
        FollowerPanel.send_position(panel)
    assert "ASSUMED AT TOWER" not in confirm.call_args.args[1]
    assert "last reported GPS" in confirm.call_args.args[1]


def test_position_input_changes_invalidate_cached_or_pending_status(panel):
    FollowerPanel.invalidate_status(panel)
    assert panel.status is None
    assert panel.status_at == 0
    assert panel.input_revision == 1
    with pytest.raises(ValueError, match="Check follower"):
        panel.require_status()
    panel.future = Future()
    panel.future.set_result({
        "status": True, "gps_ok": True, "pos_ok": True, "lat": 55, "lon": 10,
        "alt_rel_m": 20, "pos_ned": [0, 0, -20],
    })
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert panel.status is None
    assert "Position inputs changed" in panel.status_text.set.call_args.args[0]


def test_receiver_error_cannot_be_bypassed_by_assumption(panel):
    panel.future = Future()
    panel.future.set_result({"ok": False, "error": "receiver fault"})
    FollowerPanel.poll(panel, {"cmd": "status"})
    assert panel.status is None
    assert "receiver fault" in panel.status_text.set.call_args.args[0]


def test_invalid_tower_coordinates_prevent_check_when_assumption_selected(panel):
    panel.latitude.get.return_value = ""
    FollowerPanel.start_request(panel, {"cmd": "status"})
    panel.executor.submit.assert_not_called()
    assert "Latitude" in panel.status_text.set.call_args.args[0]


@pytest.fixture
def gui():
    now = time.monotonic()
    return Mock(
        compass_heading=90.0, compass_received_at=now,
        cue_system_at=0.0, cue_motor_at=now, cue_detection_at=0.0,
        cue_system={}, cue_detection={},
        cue_motor={"connected": True, "moving": False},
    )


def test_cue_bearing_is_live_antenna_compass_without_double_motor_angle(gui):
    assert OperatorGui.follower_bearing(gui) == 90


@pytest.mark.parametrize("condition", [
    "compass_stale", "motor_stale", "moving", "disconnected",
])
def test_cue_still_requires_fresh_compass_and_stationary_motor(gui, condition):
    if condition == "compass_stale":
        gui.compass_received_at -= 3
    elif condition == "motor_stale":
        gui.cue_motor_at -= 2
    elif condition == "moving":
        gui.cue_motor["moving"] = True
    elif condition == "disconnected":
        gui.cue_motor["connected"] = False
    with pytest.raises(ValueError):
        OperatorGui.follower_bearing(gui)


@pytest.mark.parametrize("mode", ["idle", "focus", "explore", "tracking"])
def test_cue_does_not_require_tracking_confirmation_or_rf_signal(gui, mode):
    gui.cue_system = {
        "mode": mode, "tracking_available": False, "target_lost": True,
        "tracking_behavior": "oscillate",
    }
    gui.cue_detection = {"viable": False}
    assert OperatorGui.follower_bearing(gui) == 90
