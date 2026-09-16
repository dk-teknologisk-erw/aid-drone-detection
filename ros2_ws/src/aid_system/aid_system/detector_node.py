import json
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, String

from .detection import BaselineModel
from .ros_utils import run_node


class DetectorNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_detector")
        self.declare_parameter("baseline_path", str(Path.home() / ".ros" / "aid_baseline.json"))
        self.declare_parameter("smoothing_mhz", 5.0)
        self.declare_parameter("minimum_delta_db", 6.0)
        self.declare_parameter("minimum_z_score", 4.0)
        self.declare_parameter("std_floor_db", 1.5)
        # Flat [low, high, low, high, ...] pairs; default masks own hotspot on Wi-Fi channel 36.
        self.declare_parameter("excluded_ranges_mhz", [5170.0, 5190.0])
        self.baseline_path = Path(str(self.get_parameter("baseline_path").value))
        self.baseline = BaselineModel()
        try:
            loaded = self.baseline.load(self.baseline_path)
            self.get_logger().info(f"Baseline {'loaded' if loaded else 'not found'}: {self.baseline_path}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"Could not load baseline: {error}")
        self.mode = "idle"
        self.motor_angle = 0.0
        self.relative_heading = 0.0
        self.best_candidate: dict | None = None
        self.best_candidates: dict[str, dict] = {}
        self.detection_publisher = self.create_publisher(String, "/aid/detection", 20)
        self.status_publisher = self.create_publisher(String, "/aid/detector/status", 10)
        self.create_subscription(String, "/aid/detector/mode", self.mode_callback, 10)
        self.create_subscription(String, "/aid/rf/sweep", self.sweep_callback, 10)
        self.create_subscription(Float32, "/aid/motor/angle_deg", self.angle_callback, 20)
        self.create_subscription(Float32, "/aid/compass/relative_heading_deg", self.heading_callback, 20)

    def mode_callback(self, message: String) -> None:
        if message.data not in {"idle", "calibrate", "explore", "focus"}:
            return
        if self.mode == "calibrate" and message.data != "calibrate":
            try:
                self.baseline.save(self.baseline_path)
            except OSError as error:
                self.get_logger().error(f"Could not save baseline: {error}")
        if message.data == "calibrate" and self.mode != "calibrate":
            self.baseline.clear()
        self.mode = message.data
        self.best_candidate = None
        self.best_candidates.clear()
        self.status_publisher.publish(String(data=json.dumps({"mode": self.mode})))

    def angle_callback(self, message: Float32) -> None:
        self.motor_angle = float(message.data) % 360.0

    def heading_callback(self, message: Float32) -> None:
        self.relative_heading = float(message.data)

    def excluded_ranges(self) -> list[tuple[float, float]]:
        bounds = [float(value) for value in self.get_parameter("excluded_ranges_mhz").value]
        if len(bounds) % 2:
            self.get_logger().warning("excluded_ranges_mhz must contain low/high pairs; ignoring")
            return []
        return [(bounds[index], bounds[index + 1]) for index in range(0, len(bounds), 2)]

    def sweep_callback(self, message: String) -> None:
        try:
            sweep = json.loads(message.data)
            if sweep.get("schema") != "aid.rf_sweep.v1":
                return
            if self.mode == "calibrate":
                count = self.baseline.update(sweep)
                result = {"viable": False, "reason": "calibrating", "baseline_samples": count}
            else:
                result = self.baseline.analyze(
                    sweep,
                    float(self.get_parameter("smoothing_mhz").value),
                    float(self.get_parameter("minimum_delta_db").value),
                    float(self.get_parameter("minimum_z_score").value),
                    float(self.get_parameter("std_floor_db").value),
                    self.excluded_ranges(),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"Invalid RF sweep: {error}")
            return

        candidate = {
            "schema": "aid.detection.v1",
            "timestamp_utc": sweep.get("timestamp_utc"),
            "mode": self.mode,
            "band": sweep.get("band"),
            "antenna_angle_deg": round(self.motor_angle, 3),
            "relative_bearing_deg": round((self.motor_angle + self.relative_heading) % 360.0, 3),
            **result,
        }
        if candidate.get("viable"):
            band = str(candidate["band"])
            current_band_best = self.best_candidates.get(band)
            if current_band_best is None or float(candidate["z_score"]) > float(current_band_best["z_score"]):
                self.best_candidates[band] = candidate.copy()
            self.best_candidate = max(
                self.best_candidates.values(), key=lambda value: float(value["z_score"])
            )
        candidate["best_candidates"] = self.best_candidates
        candidate["best_candidate"] = self.best_candidate
        self.detection_publisher.publish(String(data=json.dumps(candidate, separators=(",", ":"))))


def main(args=None) -> None:
    run_node(DetectorNode, args)


if __name__ == "__main__":
    main()