import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Empty, Float32, String

from .dual_band import DualBandScheduler
from .ros_utils import run_node
from .tracking import TrackingSearch, angular_difference


class SystemControlNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_system_control")
        self.mode = "idle"
        self.speed_dps = 30.0
        self.best_candidate: dict | None = None
        self.selected_candidate: dict | None = None
        self.accepted_target: dict | None = None
        self.motor_angle = 0.0
        self.motor_moving = False
        self.absolute_heading: float | None = None
        self.rf_selection = "2.4"
        self.rf_ready_band: str | None = None
        self.dual_scheduler = DualBandScheduler(switch_seconds=5.0)
        self.pending_focus: dict | None = None
        self.focus_ready_at = 0.0
        self.focus_commanded = False
        self.focus_arrived = False
        self.tracking_behavior = "hold"
        self.manual_direction = 0
        self.manual_next_at = 0.0
        self.last_target_seen_at = 0.0
        self.target_lost = False
        self.declare_parameter("tracking_span_deg", 3.0)
        self.declare_parameter("tracking_correction_deg", 1.0)
        self.declare_parameter("tracking_deadband_db", 1.0)
        self.declare_parameter("tracking_sample_seconds", 1.0)
        self.declare_parameter("tracking_signal_timeout_seconds", 3.0)
        self.declare_parameter("manual_step_deg", 3.0)
        self.tracking_signal_timeout = float(
            self.get_parameter("tracking_signal_timeout_seconds").value
        )
        self.manual_step_deg = float(self.get_parameter("manual_step_deg").value)
        self.tracking_search = TrackingSearch(
            float(self.get_parameter("tracking_span_deg").value),
            float(self.get_parameter("tracking_correction_deg").value),
            float(self.get_parameter("tracking_deadband_db").value),
            float(self.get_parameter("tracking_sample_seconds").value),
        )
        self.motor_mode_publisher = self.create_publisher(String, "/aid/motor/mode", 10)
        self.motor_speed_publisher = self.create_publisher(Float32, "/aid/motor/speed_dps", 10)
        self.motor_target_publisher = self.create_publisher(Float32, "/aid/motor/target_deg", 10)
        self.detector_mode_publisher = self.create_publisher(String, "/aid/detector/mode", 10)
        self.detector_target_publisher = self.create_publisher(
            Float32, "/aid/detector/target_mhz", 10
        )
        self.detector_reset_publisher = self.create_publisher(
            Empty, "/aid/detector/reset", 10
        )
        self.frequency_publisher = self.create_publisher(
            Float32, "/aid/detection/freq", 10
        )
        self.relative_yaw_publisher = self.create_publisher(
            Float32, "/aid/detection/yaw_rel", 10
        )
        self.ned_yaw_publisher = self.create_publisher(
            Float32, "/aid/detection/yaw_ned", 10
        )
        self.rf_config_publisher = self.create_publisher(String, "/aid/rf/config", 10)
        self.status_publisher = self.create_publisher(String, "/aid/system/status", 10)
        self.create_subscription(String, "/aid/control", self.command_callback, 10)
        self.create_subscription(String, "/aid/detection", self.detection_callback, 20)
        self.create_subscription(Float32, "/aid/motor/angle_deg", self.angle_callback, 20)
        self.create_subscription(String, "/aid/motor/status", self.motor_status_callback, 20)
        self.create_subscription(
            Float32, "/aid/compass/heading_deg", self.heading_callback, 20
        )
        self.create_subscription(String, "/aid/rf/status", self.rf_status_callback, 10)
        self.create_timer(0.1, self.control_tick)
        self.create_timer(1.0, self.publish_status)
        self.get_logger().info("AID system control ready")

    def detection_callback(self, message: String) -> None:
        try:
            detection = json.loads(message.data)
            if self.mode == "explore":
                self.best_candidate = detection.get("best_candidate")
            elif self.mode == "tracking" and self.accepted_target is not None:
                if detection.get("viable"):
                    self.last_target_seen_at = time.monotonic()
                if "delta_db" in detection:
                    self.tracking_search.add_sample(float(detection["delta_db"]))
        except (TypeError, ValueError, json.JSONDecodeError):
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

    def heading_callback(self, message: Float32) -> None:
        self.absolute_heading = float(message.data) % 360.0

    def motor_status_callback(self, message: String) -> None:
        try:
            status = json.loads(message.data)
            if not status.get("connected", False):
                return
            self.motor_moving = bool(status.get("moving", False))
            angle = float(status.get("angle_deg", self.motor_angle)) % 360.0
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        now = time.monotonic()
        if (
            self.mode == "focus"
            and self.selected_candidate is not None
            and self.focus_commanded
            and not self.motor_moving
            and abs(angular_difference(float(self.selected_candidate["antenna_angle_deg"]), angle)) <= 1.0
        ):
            self.focus_arrived = True
        if (
            self.mode == "tracking"
            and self.tracking_behavior == "oscillate"
            and self.manual_direction == 0
            and not self.target_lost
            and not self.motor_moving
            and self.tracking_search.target_deg is not None
            and abs(angular_difference(self.tracking_search.target_deg, angle)) <= 1.0
        ):
            self.tracking_search.target_reached(now)

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
            elif name == "accept":
                self.accept_selected_candidate()
            elif name == "decline":
                self.decline_selected_candidate()
            elif name == "discard":
                self.discard_candidate()
            elif name == "tracking_behavior":
                self.set_tracking_behavior(str(command["behavior"]))
            elif name == "manual":
                self.set_manual_motion(
                    str(command["action"]), str(command.get("direction", ""))
                )
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
        if mode not in {"idle", "calibrate", "explore", "focus", "tracking"}:
            raise ValueError(f"invalid mode: {mode}")
        if mode == "tracking":
            self.start_tracking()
            return
        self.mode = mode
        self.detector_mode_publisher.publish(String(data=mode))
        self.pending_focus = None
        self.focus_commanded = False
        self.focus_arrived = False
        self.manual_direction = 0
        self.tracking_search.reset(self.motor_angle)
        if mode in {"calibrate", "explore"}:
            self.best_candidate = None
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
        self.selected_candidate = self.best_candidate.copy()
        self.best_candidate = None
        self.detector_reset_publisher.publish(Empty())
        target = float(self.selected_candidate["antenna_angle_deg"]) % 360.0
        candidate_band = {"2.4ghz": "2.4", "5.8ghz": "5.8"}.get(
            self.selected_candidate.get("band")
        )
        self.mode = "focus"
        self.detector_mode_publisher.publish(String(data="focus"))
        self.dual_scheduler.cancel()
        self.focus_commanded = False
        self.focus_arrived = False
        if self.rf_selection == "dual" and candidate_band and candidate_band != self.rf_ready_band:
            self.pending_focus = {"target": target, "band": candidate_band}
            self.focus_ready_at = time.monotonic() + 5.0
            self.motor_mode_publisher.publish(String(data="stop"))
            self.publish_rf_band(candidate_band)
        else:
            self.pending_focus = None
            self.command_focus_target(target)
        self.publish_status()

    def command_focus_target(self, target: float) -> None:
        self.focus_commanded = True
        self.focus_arrived = False
        self.motor_target_publisher.publish(Float32(data=target % 360.0))
        self.motor_mode_publisher.publish(String(data="focus"))

    def accept_selected_candidate(self) -> None:
        if self.selected_candidate is None or not self.focus_arrived:
            raise ValueError("focused candidate has not reached its target")
        self.accepted_target = self.selected_candidate.copy()
        self.selected_candidate = None
        self.start_tracking()

    def decline_selected_candidate(self) -> None:
        if self.selected_candidate is None or not self.focus_arrived:
            raise ValueError("focused candidate has not reached its target")
        self.selected_candidate = None
        self.detector_reset_publisher.publish(Empty())
        self.set_mode("explore")

    def discard_candidate(self) -> None:
        self.best_candidate = None
        self.selected_candidate = None
        self.accepted_target = None
        self.pending_focus = None
        self.detector_reset_publisher.publish(Empty())
        self.set_mode("idle")

    def start_tracking(self) -> None:
        if self.accepted_target is None:
            raise ValueError("no accepted target available")
        frequency = float(self.accepted_target["frequency_mhz"])
        self.mode = "tracking"
        self.focus_commanded = False
        self.focus_arrived = False
        self.pending_focus = None
        self.dual_scheduler.cancel()
        self.target_lost = False
        self.last_target_seen_at = time.monotonic()
        self.detector_target_publisher.publish(Float32(data=frequency))
        self.detector_mode_publisher.publish(String(data="tracking"))
        self.motor_mode_publisher.publish(String(data="stop"))
        self.tracking_search.reset(self.motor_angle)
        if self.tracking_behavior == "oscillate":
            self.publish_tracking_target(self.tracking_search.start(self.motor_angle))
        self.publish_tracking_outputs()
        self.publish_status()

    def set_tracking_behavior(self, behavior: str) -> None:
        if behavior not in {"hold", "oscillate"}:
            raise ValueError(f"invalid tracking behavior: {behavior}")
        self.tracking_behavior = behavior
        self.manual_direction = 0
        self.tracking_search.reset(self.motor_angle)
        if self.mode == "tracking":
            self.motor_mode_publisher.publish(String(data="stop"))
            if behavior == "oscillate" and not self.target_lost:
                self.publish_tracking_target(
                    self.tracking_search.start(self.motor_angle)
                )
        self.publish_status()

    def set_manual_motion(self, action: str, direction: str) -> None:
        if self.mode != "tracking":
            raise ValueError("manual tracking motion requires Tracking mode")
        if action == "start":
            if direction not in {"clockwise", "counterclockwise"}:
                raise ValueError(f"invalid manual direction: {direction}")
            self.manual_direction = 1 if direction == "clockwise" else -1
            self.tracking_search.reset(self.motor_angle)
            self.manual_next_at = 0.0
        elif action == "stop":
            self.manual_direction = 0
            self.motor_mode_publisher.publish(String(data="stop"))
            self.tracking_search.reset(self.motor_angle)
            if self.tracking_behavior == "oscillate" and not self.target_lost:
                self.publish_tracking_target(
                    self.tracking_search.start(self.motor_angle)
                )
        else:
            raise ValueError(f"invalid manual action: {action}")
        self.publish_status()

    def publish_tracking_target(self, target: float) -> None:
        self.motor_target_publisher.publish(Float32(data=target % 360.0))
        self.motor_mode_publisher.publish(String(data="focus"))

    def publish_tracking_outputs(self) -> None:
        if self.mode != "tracking" or self.accepted_target is None:
            return
        self.frequency_publisher.publish(
            Float32(data=float(self.accepted_target["frequency_mhz"]))
        )
        self.relative_yaw_publisher.publish(Float32(data=self.motor_angle % 360.0))
        if self.absolute_heading is not None:
            self.ned_yaw_publisher.publish(
                Float32(data=self.absolute_heading % 360.0)
            )

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
            self.command_focus_target(float(self.pending_focus["target"]))
            self.pending_focus = None
            self.publish_status()
        if self.mode != "tracking" or self.accepted_target is None:
            return
        self.publish_tracking_outputs()
        if self.manual_direction:
            if now >= self.manual_next_at:
                target = self.motor_angle + self.manual_direction * self.manual_step_deg
                self.publish_tracking_target(target)
                self.manual_next_at = now + 0.2
            return
        if now - self.last_target_seen_at >= self.tracking_signal_timeout:
            if not self.target_lost:
                self.target_lost = True
                self.manual_direction = 0
                self.tracking_search.reset(self.motor_angle)
                self.motor_mode_publisher.publish(String(data="stop"))
                self.publish_status()
            return
        if self.tracking_behavior == "oscillate":
            target = self.tracking_search.advance(now)
            if target is not None:
                self.publish_tracking_target(target)

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
            "focus_available": bool(
                self.best_candidate and self.best_candidate.get("viable")
            ),
            "confirmation_available": bool(
                self.selected_candidate is not None and self.focus_arrived
            ),
            "candidate_selected": self.selected_candidate is not None,
            "tracking_available": self.accepted_target is not None,
            "tracking_behavior": self.tracking_behavior,
            "tracking_phase": self.tracking_search.phase,
            "target_lost": self.target_lost,
            "tracked_frequency_mhz": (
                float(self.accepted_target["frequency_mhz"])
                if self.accepted_target is not None
                else None
            ),
            "manual_direction": self.manual_direction,
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