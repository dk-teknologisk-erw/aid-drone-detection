import json
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import Empty, Float32, String

from .detection import BaselineModel, CandidateHistory, analyze_without_baseline, live_maximum
from .ros_utils import run_node


class DetectorNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_detector")
        self.declare_parameter("baseline_path", str(Path.home() / ".ros" / "aid_baseline.json"))
        self.declare_parameter("smoothing_mhz", 5.0)
        self.declare_parameter("minimum_delta_db", 6.0)
        self.declare_parameter("minimum_z_score", 4.0)
        self.declare_parameter("std_floor_db", 1.5)
        self.declare_parameter("detection_method", "no_baseline")
        self.declare_parameter("minimum_snr_db", 6.0)
        self.declare_parameter("tracking_minimum_snr_db", 4.0)
        self.declare_parameter("tracking_minimum_delta_db", 4.0)
        self.declare_parameter("tracking_minimum_z_score", 3.0)
        self.declare_parameter("tracking_window_mhz", 10.0)
        self.declare_parameter("candidate_lifetime_seconds", 45.0)
        self.declare_parameter("excluded_ranges_mhz", [2402.0, 2422.0])
        self.baseline_path = Path(str(self.get_parameter("baseline_path").value))
        excluded_values = [
            float(value) for value in self.get_parameter("excluded_ranges_mhz").value
        ]
        if len(excluded_values) % 2:
            raise ValueError("excluded_ranges_mhz must contain low/high pairs")
        self.excluded_ranges_mhz = list(
            zip(excluded_values[::2], excluded_values[1::2])
        )
        self.baseline = BaselineModel()
        try:
            loaded = self.baseline.load(self.baseline_path)
            self.get_logger().info(f"Baseline {'loaded' if loaded else 'not found'}: {self.baseline_path}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"Could not load baseline: {error}")
        self.mode = "idle"
        self.method = str(self.get_parameter("detection_method").value)
        if self.method not in {"baseline", "no_baseline"}:
            raise ValueError("detection_method must be baseline or no_baseline")
        self.motor_angle = 0.0
        self.relative_heading = 0.0
        self.target_frequency_mhz: float | None = None
        self.history = CandidateHistory(
            float(self.get_parameter("candidate_lifetime_seconds").value)
        )
        self.detection_publisher = self.create_publisher(String, "/aid/detection", 20)
        self.status_publisher = self.create_publisher(String, "/aid/detector/status", 10)
        self.create_subscription(String, "/aid/detector/mode", self.mode_callback, 10)
        self.create_subscription(String, "/aid/detector/method", self.method_callback, 10)
        self.create_timer(1.0, self.publish_status)
        self.create_subscription(
            Float32,
            "/aid/detector/target_mhz",
            self.target_frequency_callback,
            10,
        )
        self.create_subscription(Empty, "/aid/detector/reset", self.reset_callback, 10)
        self.create_subscription(String, "/aid/rf/sweep", self.sweep_callback, 10)
        self.create_subscription(Float32, "/aid/motor/angle_deg", self.angle_callback, 20)
        self.create_subscription(Float32, "/aid/compass/relative_heading_deg", self.heading_callback, 20)

    def mode_callback(self, message: String) -> None:
        if message.data not in {"idle", "calibrate", "explore", "focus", "tracking"}:
            return
        if self.mode == "calibrate" and message.data != "calibrate":
            try:
                self.baseline.save(self.baseline_path)
            except OSError as error:
                self.get_logger().error(f"Could not save baseline: {error}")
        if message.data == "calibrate" and self.mode != "calibrate":
            self.baseline.clear()
            self.method = "baseline"
        self.mode = message.data
        if self.mode not in {"focus", "tracking"}:
            self.target_frequency_mhz = None
        self.history.clear()
        self.publish_status()

    def publish_status(self) -> None:
        self.status_publisher.publish(String(data=json.dumps({
            "mode": self.mode, "method": self.method,
        })))

    def method_callback(self, message: String) -> None:
        if message.data not in {"baseline", "no_baseline"}:
            self.get_logger().warning(f"Invalid detection method: {message.data}")
            return
        self.method = message.data
        self.history.clear()
        self.publish_status()

    def target_frequency_callback(self, message: Float32) -> None:
        self.target_frequency_mhz = float(message.data)

    def reset_callback(self, _message: Empty) -> None:
        self.history.clear()

    def angle_callback(self, message: Float32) -> None:
        self.motor_angle = float(message.data) % 360.0

    def heading_callback(self, message: Float32) -> None:
        self.relative_heading = float(message.data)

    def sweep_callback(self, message: String) -> None:
        try:
            sweep = json.loads(message.data)
            if sweep.get("schema") != "aid.rf_sweep.v1":
                return
            maximum = live_maximum(sweep)
            following = self.mode in {"focus", "tracking"}
            retaining = self.mode == "tracking"
            target = self.target_frequency_mhz if following else None
            if self.mode == "calibrate":
                count = self.baseline.update(sweep)
                result = {"viable": False, "reason": "calibrating", "baseline_samples": count}
            elif self.mode == "tracking" and self.target_frequency_mhz is None:
                result = {"viable": False, "reason": "no_tracking_target"}
            elif self.method == "no_baseline":
                result = analyze_without_baseline(
                    sweep,
                    float(self.get_parameter("smoothing_mhz").value),
                    float(self.get_parameter(
                        "tracking_minimum_snr_db" if retaining else "minimum_snr_db"
                    ).value),
                    self.excluded_ranges_mhz,
                    target,
                    float(self.get_parameter("tracking_window_mhz").value),
                )
            else:
                result = self.baseline.analyze(
                    sweep,
                    float(self.get_parameter("smoothing_mhz").value),
                    float(self.get_parameter(
                        "tracking_minimum_delta_db" if retaining else "minimum_delta_db"
                    ).value),
                    float(self.get_parameter(
                        "tracking_minimum_z_score" if retaining else "minimum_z_score"
                    ).value),
                    float(self.get_parameter("std_floor_db").value),
                    self.excluded_ranges_mhz,
                    target,
                    float(self.get_parameter("tracking_window_mhz").value),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"Invalid RF sweep: {error}")
            return

        candidate = {
            "schema": "aid.detection.v1",
            "timestamp_utc": sweep.get("timestamp_utc"),
            "mode": self.mode,
            "band": sweep.get("band"),
            "method": self.method,
            "live_maximum": maximum,
            "antenna_angle_deg": round(self.motor_angle, 3),
            "relative_bearing_deg": round((self.motor_angle + self.relative_heading) % 360.0, 3),
            **result,
        }
        now = time.monotonic()
        if self.mode == "explore":
            self.history.observe(candidate, now)
        best_candidates = self.history.candidates(now)
        candidate["best_candidates"] = best_candidates
        candidate["best_candidate"] = max(
            best_candidates.values(), key=lambda value: float(value["score"]), default=None
        )
        self.detection_publisher.publish(String(data=json.dumps(candidate, separators=(",", ":"))))


def main(args=None) -> None:
    run_node(DetectorNode, args)


if __name__ == "__main__":
    main()