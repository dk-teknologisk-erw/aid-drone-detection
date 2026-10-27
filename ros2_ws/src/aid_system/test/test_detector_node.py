import json

import pytest
import rclpy
from std_msgs.msg import Float32, String

from aid_system.detector_node import DetectorNode


@pytest.fixture
def detector(tmp_path):
    rclpy.init(args=[
        "--ros-args", "-p", f"baseline_path:={tmp_path / 'baseline.json'}",
    ])
    node = DetectorNode()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def sweep():
    return {
        "schema": "aid.rf_sweep.v1", "band": "5.8ghz",
        "start_mhz": 5725.0, "step_mhz": 1.0,
        "amplitudes_dbm": [-95.0] * 50 + [-65.0] * 7 + [-95.0] * 69,
    }


def test_no_baseline_startup_and_repeated_candidate(detector):
    assert detector.method == "no_baseline"
    assert not detector.baseline.configurations
    detector.mode_callback(String(data="explore"))
    detector.sweep_callback(String(data=json.dumps(sweep())))
    assert not detector.history.best
    detector.sweep_callback(String(data=json.dumps(sweep())))
    assert detector.history.best["5.8ghz"][0]["viable"]


def test_calibration_persists_and_saved_baseline_can_be_reused(detector):
    detector.mode_callback(String(data="calibrate"))
    assert detector.method == "baseline"
    detector.sweep_callback(String(data=json.dumps(sweep())))
    detector.sweep_callback(String(data=json.dumps(sweep())))
    detector.mode_callback(String(data="idle"))
    assert detector.baseline_path.exists()
    detector.baseline.clear()
    assert detector.baseline.load(detector.baseline_path)
    assert next(iter(detector.baseline.configurations.values()))["count"] == 2
    detector.method_callback(String(data="no_baseline"))
    assert detector.method == "no_baseline"
    assert detector.baseline.configurations


def test_maximum_published_without_baseline_and_in_all_operation_modes(detector):
    received = []
    detector.create_subscription(
        String, "/aid/detection", lambda message: received.append(json.loads(message.data)), 20,
    )
    record = sweep()
    record["amplitudes_dbm"][100] = -40.0
    for mode in ("idle", "calibrate", "explore", "focus", "tracking"):
        detector.mode_callback(String(data=mode))
        detector.sweep_callback(String(data=json.dumps(record)))
        for _ in range(20):
            rclpy.spin_once(detector, timeout_sec=0.05)
            if received and received[-1]["mode"] == mode:
                break
        assert received[-1]["mode"] == mode
        assert received[-1]["live_maximum"] == {
            "frequency_mhz": 5825.0, "amplitude_dbm": -40.0,
        }


def test_tracking_target_is_cleared_when_exploring(detector):
    detector.mode_callback(String(data="tracking"))
    detector.target_frequency_callback(Float32(data=5780.0))
    detector.mode_callback(String(data="explore"))
    assert detector.target_frequency_mhz is None
