import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, String

from .dual_band import DualBandScheduler
from .ros_utils import run_node


class SystemControlNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_system_control")
        self.mode = "idle"
        self.speed_dps = 30.0
        self.best_candidate: dict | None = None
        self.motor_angle = 0.0
        self.rf_selection = "2.4"
        self.rf_ready_band: str | None = None
        self.dual_scheduler = DualBandScheduler(switch_seconds=5.0)
        self.pending_focus: dict | None = None
        self.focus_ready_at = 0.0
        self.motor_mode_publisher = self.create_publisher(String, "/aid/motor/mode", 10)
        self.motor_speed_publisher = self.create_publisher(Float32, "/aid/motor/speed_dps", 10)
        self.motor_target_publisher = self.create_publisher(Float32, "/aid/motor/target_deg", 10)
        self.detector_mode_publisher = self.create_publisher(String, "/aid/detector/mode", 10)
        self.rf_config_publisher = self.create_publisher(String, "/aid/rf/config", 10)
        self.status_publisher = self.create_publisher(String, "/aid/system/status", 10)
        self.create_subscription(String, "/aid/control", self.command_callback, 10)
        self.create_subscription(String, "/aid/detection", self.detection_callback, 20)
        self.create_subscription(Float32, "/aid/motor/angle_deg", self.angle_callback, 20)
        self.create_subscription(String, "/aid/rf/status", self.rf_status_callback, 10)
        self.create_timer(0.1, self.control_tick)
        self.create_timer(1.0, self.publish_status)
        self.get_logger().info("AID system control ready")

    def detection_callback(self, message: String) -> None:
        try:
            detection = json.loads(message.data)
            self.best_candidate = detection.get("best_candidate")
        except json.JSONDecodeError:
            pass

    def angle_callback(self, message: Float32) -> None:
        self.motor_angle = float(message.data) % 360.0
        if self.rf_selection != "dual" or self.mode not in {"calibrate", "explore"}:
            return
        next_band = self.dual_scheduler.observe_angle(self.motor_angle, time.monotonic())
        if next_band is not None:
            self.motor_mode_publisher.publish(String(data="stop"))
            self.publish_rf_band(next_band)
            self.publish_status()

    def rf_status_callback(self, message: String) -> None:
        try:
            status = json.loads(message.data)
        except json.JSONDecodeError:
            return
        if status.get("state") != "scanning":
            return
        self.rf_ready_band = {
            "2.4ghz": "2.4",
            "5.8ghz": "5.8",
        }.get(status.get("selection"))

    def command_callback(self, message: String) -> None:
        try:
            command = json.loads(message.data)
            name = command["command"]
            if name == "mode":
                self.set_mode(str(command["mode"]))
            elif name == "focus":
                self.focus_best_candidate()
            elif name == "speed":
                self.speed_dps = max(0.1, min(90.0, float(command["speed_dps"])))
                self.motor_speed_publisher.publish(Float32(data=self.speed_dps))
            elif name == "rf":
                self.configure_rf(command)
            elif name == "target":
                self.pending_focus = None
                self.dual_scheduler.cancel()
                self.motor_target_publisher.publish(Float32(data=float(command["angle_deg"]) % 360.0))
                self.set_mode("focus")
            else:
                raise ValueError(f"unknown command: {name}")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.publish_status(error=str(error))

    def set_mode(self, mode: str) -> None:
        if mode not in {"idle", "calibrate", "explore", "focus"}:
            raise ValueError(f"invalid mode: {mode}")
        self.mode = mode
        self.detector_mode_publisher.publish(String(data=mode))
        self.pending_focus = None
        if mode in {"calibrate", "explore"} and self.rf_selection == "dual":
            band = self.rf_ready_band if self.rf_ready_band in DualBandScheduler.BANDS else "2.4"
            self.publish_rf_band(band)
            if self.rf_ready_band == band:
                self.dual_scheduler.start_scan(band, self.motor_angle)
                self.motor_mode_publisher.publish(String(data="explore"))
            else:
                self.dual_scheduler.start_switch(band, time.monotonic(), self.motor_angle)
                self.motor_mode_publisher.publish(String(data="stop"))
        else:
            self.dual_scheduler.cancel()
            motor_mode = "explore" if mode in {"calibrate", "explore"} else "focus" if mode == "focus" else "stop"
            self.motor_mode_publisher.publish(String(data=motor_mode))
        self.publish_status()

    def configure_rf(self, command: dict) -> None:
        selection = str(command.get("selection", ""))
        if selection == "dual":
            self.rf_selection = "dual"
            band = self.rf_ready_band if self.rf_ready_band in DualBandScheduler.BANDS else "2.4"
            self.publish_rf_band(band)
            if self.mode in {"calibrate", "explore"}:
                self.motor_mode_publisher.publish(String(data="stop"))
                self.dual_scheduler.start_switch(band, time.monotonic(), self.motor_angle)
            self.publish_status()
            return

        if selection not in {"2.4", "5.8", "custom"}:
            raise ValueError(f"invalid RF selection: {selection}")
        self.rf_selection = selection
        self.dual_scheduler.cancel()
        self.pending_focus = None
        self.rf_config_publisher.publish(String(data=json.dumps(command, separators=(",", ":"))))
        if self.mode in {"calibrate", "explore"}:
            self.motor_mode_publisher.publish(String(data="explore"))
        self.publish_status()

    def publish_rf_band(self, band: str) -> None:
        self.rf_config_publisher.publish(
            String(data=json.dumps({"selection": band}, separators=(",", ":")))
        )

    def focus_best_candidate(self) -> None:
        if not self.best_candidate or not self.best_candidate.get("viable"):
            self.publish_status(error="no viable candidate available")
            return
        target = float(self.best_candidate["antenna_angle_deg"]) % 360.0
        candidate_band = {"2.4ghz": "2.4", "5.8ghz": "5.8"}.get(
            self.best_candidate.get("band")
        )
        self.mode = "focus"
        self.detector_mode_publisher.publish(String(data="focus"))
        self.dual_scheduler.cancel()
        if self.rf_selection == "dual" and candidate_band and candidate_band != self.rf_ready_band:
            self.pending_focus = {"target": target, "band": candidate_band}
            self.focus_ready_at = time.monotonic() + 5.0
            self.motor_mode_publisher.publish(String(data="stop"))
            self.publish_rf_band(candidate_band)
        else:
            self.pending_focus = None
            self.motor_target_publisher.publish(Float32(data=target))
            self.motor_mode_publisher.publish(String(data="focus"))
        self.publish_status()

    def control_tick(self) -> None:
        now = time.monotonic()
        if (
            self.rf_selection == "dual"
            and self.mode in {"calibrate", "explore"}
            and self.dual_scheduler.ready_to_resume(now, self.rf_ready_band, self.motor_angle)
        ):
            self.motor_mode_publisher.publish(String(data="explore"))
            self.publish_status()
        if (
            self.pending_focus is not None
            and now >= self.focus_ready_at
            and self.rf_ready_band == self.pending_focus["band"]
        ):
            self.motor_target_publisher.publish(Float32(data=self.pending_focus["target"]))
            self.motor_mode_publisher.publish(String(data="focus"))
            self.pending_focus = None
            self.publish_status()

    def publish_status(self, error: str | None = None) -> None:
        now = time.monotonic()
        phase = self.dual_scheduler.phase if self.rf_selection == "dual" else "single"
        switch_remaining = 0.0
        if self.pending_focus is not None:
            phase = "focus_switching"
            switch_remaining = max(0.0, self.focus_ready_at - now)
        elif phase == "switching":
            switch_remaining = max(0.0, self.dual_scheduler.switch_ready_at - now)
        status = {
            "mode": self.mode,
            "speed_dps": self.speed_dps,
            "focus_available": bool(self.best_candidate),
            "rf_selection": self.rf_selection,
            "active_band": self.dual_scheduler.active_band if self.rf_selection == "dual" else self.rf_ready_band,
            "phase": phase,
            "rotation_progress_deg": round(self.dual_scheduler.rotation_degrees, 1),
            "switch_remaining_s": round(switch_remaining, 1),
        }
        if error:
            status["error"] = error
        self.status_publisher.publish(String(data=json.dumps(status, separators=(",", ":"))))


def main(args=None) -> None:
    run_node(SystemControlNode, args)


if __name__ == "__main__":
    main()