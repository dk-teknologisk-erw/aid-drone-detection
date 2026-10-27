import json
import math
from unittest.mock import Mock

import pytest
from std_msgs.msg import Empty, String

from aid_system.compass_node import CompassNode, update_signed_heading
from aid_system.operator_gui import OperatorGui


@pytest.mark.parametrize("heading", [0.0, 90.0, 180.0, 270.0, 359.0])
def test_signed_heading_starts_at_magnetic_heading(heading):
    assert update_signed_heading(heading, None) == heading


@pytest.mark.parametrize(
    ("headings", "expected"),
    [
        ([10.0, 1.0, 359.0, 350.0, 270.0, 180.0, 90.0, 1.0, 0.0, 359.0],
         [10.0, 1.0, -1.0, -10.0, -90.0, -180.0, -270.0, -359.0, 0.0, -1.0]),
        ([350.0, 359.0, 0.0, 1.0, 90.0, 180.0, 270.0, 359.0, 0.0],
         [350.0, 359.0, 0.0, 1.0, 90.0, 180.0, 270.0, 359.0, 0.0]),
        ([5.0, 359.0, 1.0, 359.0, 1.0], [5.0, -1.0, 1.0, -1.0, 1.0]),
    ],
)
def test_signed_heading_crosses_north_and_wraps_at_full_turn(headings, expected):
    previous = None
    actual = []
    for heading in headings:
        previous = update_signed_heading(heading, previous)
        actual.append(previous)
    assert actual == expected


@pytest.mark.parametrize("direction", [-1, 1])
def test_signed_heading_stays_bounded_over_multiple_turns(direction):
    previous = None
    for angle in range(0, 1441):
        heading = (direction * angle) % 360.0
        previous = update_signed_heading(heading, previous)
        assert -360.0 < previous < 360.0
        assert previous % 360.0 == heading


def test_compass_zero_does_not_change_magnetic_heading():
    node = Mock(reference_heading=None, latest_heading=None, signed_heading=None)
    node.sensor.axes.return_value = (0, 1000, 1500)

    CompassNode.sample(node)

    status = json.loads(node.status_publisher.publish.call_args.args[0].data)
    assert status["heading_deg"] == 90.0
    assert status["signed_heading_deg"] == 90.0
    assert status["relative_heading_deg"] == 0.0

    CompassNode.zero_callback(node, Empty())
    node.sensor.axes.return_value = (1000, -18, 1500)
    CompassNode.sample(node)

    status = json.loads(node.status_publisher.publish.call_args.args[0].data)
    assert status["signed_heading_deg"] == pytest.approx(-1.031, abs=0.001)
    assert status["heading_deg"] == pytest.approx(358.969, abs=0.001)
    assert node.absolute_heading_publisher.publish.call_args.args[0].data == pytest.approx(
        math.degrees(math.atan2(-18, 1000)) % 360.0
    )

    CompassNode.zero_callback(node, Empty())
    CompassNode.sample(node)

    status = json.loads(node.status_publisher.publish.call_args.args[0].data)
    assert status["signed_heading_deg"] == pytest.approx(-1.031, abs=0.001)
    assert status["relative_heading_deg"] == 0.0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"heading_deg": 359.0, "signed_heading_deg": -1.0, "relative_heading_deg": 20.0},
         "Magnetic heading: -1.0 deg"),
        ({"heading_deg": 90.0, "relative_heading_deg": 0.0},
         "Magnetic heading: +90.0 deg"),
        ({"relative_heading_deg": 20.0}, "Magnetic heading: waiting"),
    ],
)
def test_gui_displays_magnetic_heading_instead_of_startup_relative(status, expected):
    gui = Mock()
    gui.decode.return_value = status

    OperatorGui.compass_callback(gui, String(data=json.dumps(status)))

    gui.compass_text.set.assert_called_once_with(expected)