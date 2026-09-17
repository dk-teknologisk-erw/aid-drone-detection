import json
import math
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk, UnidentifiedImageError
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from .camera_preview import decode_preview


PREVIEW_SIZE = (480, 270)


class OperatorGui(Node):
    def __init__(self) -> None:
        super().__init__("aid_operator_gui")
        self.control_publisher = self.create_publisher(String, "/aid/control", 10)
        self.create_subscription(String, "/aid/system/status", self.system_callback, 10)
        self.create_subscription(String, "/aid/motor/status", self.motor_callback, 20)
        self.create_subscription(String, "/aid/rf/status", self.rf_callback, 10)
        self.create_subscription(String, "/aid/detection", self.detection_callback, 20)
        self.create_subscription(String, "/aid/compass/status", self.compass_callback, 10)
        self.declare_parameter("camera_topic", "/aid_camera/image_raw/compressed")

        self.root = tk.Tk()
        self.root.title("AID Direction Finder")
        self.root.geometry("760x560")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.mode_text = tk.StringVar(value="Mode: unknown")
        self.motor_text = tk.StringVar(value="Motor: waiting")
        self.rf_text = tk.StringVar(value="RF: waiting")
        self.compass_text = tk.StringVar(value="Heading: waiting")
        self.candidate_text = tk.StringVar(value="No candidate")
        self.speed = tk.DoubleVar(value=30.0)
        self.manual_target = tk.StringVar(value="0")
        self.rf_selection = tk.StringVar(value="2.4")
        self.center = tk.StringVar(value="5745")
        self.span = tk.StringVar(value="20")
        self.focus_available = False
        self.preview_window: tk.Toplevel | None = None
        self.preview_label: ttk.Label | None = None
        self.preview_status = tk.StringVar(value="Waiting for camera...")
        self.preview_subscription = None
        self.preview_data: bytes | None = None
        self.preview_frame_number = 0
        self.displayed_frame_number = 0
        self.preview_photo = None
        self.build_ui()

    def build_ui(self) -> None:
        root = ttk.Frame(self.root, padding=14)
        root.pack(fill="both", expand=True)

        mode_frame = ttk.LabelFrame(root, text="Operation", padding=10)
        mode_frame.pack(fill="x")
        for label, mode in (("Calibrate", "calibrate"), ("Explore", "explore"), ("Stop", "idle")):
            ttk.Button(mode_frame, text=label, command=lambda selected=mode: self.send(command="mode", mode=selected)).pack(side="left", padx=4)
        self.focus_button = ttk.Button(mode_frame, text="Focus best", command=lambda: self.send(command="focus"), state="disabled")
        self.focus_button.pack(side="left", padx=4)
        ttk.Button(mode_frame, text="Preview", command=self.open_preview).pack(side="left", padx=4)
        ttk.Label(mode_frame, textvariable=self.mode_text).pack(side="right")

        motor_frame = ttk.LabelFrame(root, text="Rotation speed", padding=10)
        motor_frame.pack(fill="x", pady=(10, 0))
        ttk.Scale(motor_frame, from_=1.0, to=90.0, variable=self.speed, orient="horizontal").pack(side="left", fill="x", expand=True)
        self.speed_label = ttk.Label(motor_frame, width=10)
        self.speed_label.pack(side="left", padx=8)
        ttk.Button(motor_frame, text="Apply", command=self.apply_speed).pack(side="left")
        self.speed.trace_add("write", lambda *_: self.speed_label.configure(text=f"{self.speed.get():.1f} deg/s"))
        self.speed.set(30.0)

        target_frame = ttk.LabelFrame(root, text="Manual focus", padding=10)
        target_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(target_frame, text="Orientation").pack(side="left")
        ttk.Spinbox(
            target_frame,
            from_=0.0,
            to=360.0,
            increment=1.0,
            textvariable=self.manual_target,
            width=8,
        ).pack(side="left", padx=8)
        ttk.Label(target_frame, text="deg").pack(side="left")
        ttk.Button(target_frame, text="Focus orientation", command=self.apply_manual_target).pack(side="left", padx=12)

        rf_frame = ttk.LabelFrame(root, text="RF scan", padding=10)
        rf_frame.pack(fill="x", pady=(10, 0))
        for label, value in (("Dual", "dual"), ("2.4 GHz", "2.4"), ("5.8 GHz", "5.8"), ("Custom", "custom")):
            ttk.Radiobutton(rf_frame, text=label, variable=self.rf_selection, value=value).pack(side="left")
        ttk.Label(rf_frame, text="Center MHz").pack(side="left", padx=(14, 4))
        ttk.Entry(rf_frame, textvariable=self.center, width=8).pack(side="left")
        ttk.Label(rf_frame, text="Span MHz").pack(side="left", padx=(8, 4))
        ttk.Entry(rf_frame, textvariable=self.span, width=6).pack(side="left")
        ttk.Button(rf_frame, text="Apply", command=self.apply_rf).pack(side="right")

        status_frame = ttk.LabelFrame(root, text="Live status", padding=10)
        status_frame.pack(fill="both", expand=True, pady=(10, 0))
        for variable in (self.motor_text, self.rf_text, self.compass_text, self.candidate_text):
            ttk.Label(status_frame, textvariable=variable, anchor="w", justify="left", wraplength=700).pack(fill="x", pady=5)

    def send(self, command: str = "mode", mode: str | None = None, **fields) -> None:
        if mode is not None:
            fields["mode"] = mode
        fields["command"] = command
        self.control_publisher.publish(String(data=json.dumps(fields, separators=(",", ":"))))

    def apply_speed(self) -> None:
        self.send(command="speed", speed_dps=self.speed.get())

    def apply_manual_target(self) -> None:
        try:
            target = float(self.manual_target.get())
            if not math.isfinite(target) or not 0.0 <= target <= 360.0:
                raise ValueError
            self.send(command="target", angle_deg=target % 360.0)
        except ValueError:
            self.motor_text.set("Motor: orientation must be between 0 and 360 degrees")

    def apply_rf(self) -> None:
        try:
            self.send(command="rf", selection=self.rf_selection.get(), center_mhz=float(self.center.get()), span_mhz=float(self.span.get()))
        except ValueError:
            self.rf_text.set("RF: center and span must be numbers")

    def system_callback(self, message: String) -> None:
        data = self.decode(message)
        phase = data.get("phase", "")
        band = data.get("active_band", "")
        remaining = float(data.get("switch_remaining_s", 0.0))
        suffix = f" | {phase} {band}" if phase else ""
        if remaining > 0.0:
            suffix += f" | resume in {remaining:.1f}s"
        self.mode_text.set(f"Mode: {data.get('mode', 'unknown')}{suffix}")

    def motor_callback(self, message: String) -> None:
        data = self.decode(message)
        if not data.get("connected", False):
            self.motor_text.set(f"Motor: disconnected ({data.get('error', 'waiting')})")
            return
        self.motor_text.set(
            f"Motor: {data.get('mode')} | angle {float(data.get('angle_deg', 0)):.1f} deg | "
            f"speed {float(data.get('speed_dps', 0)):.1f} deg/s"
        )

    def rf_callback(self, message: String) -> None:
        data = self.decode(message)
        if not data.get("connected", False):
            self.rf_text.set(f"RF: {data.get('state')} ({data.get('error', 'waiting')})")
            return
        self.rf_text.set(
            f"RF: {data.get('state')} | {data.get('selection', '')} "
            f"{data.get('start_mhz', '')}-{data.get('stop_mhz', '')} MHz"
        )

    def compass_callback(self, message: String) -> None:
        data = self.decode(message)
        self.compass_text.set(f"Relative heading: {float(data.get('relative_heading_deg', 0)):.1f} deg")

    def detection_callback(self, message: String) -> None:
        data = self.decode(message)
        best = data.get("best_candidate")
        self.focus_available = bool(best and best.get("viable"))
        self.focus_button.configure(state="normal" if self.focus_available else "disabled")
        if best:
            self.candidate_text.set(
                f"Best candidate: {float(best['frequency_mhz']):.3f} MHz | "
                f"antenna {float(best['antenna_angle_deg']):.1f} deg | "
                f"relative bearing {float(best['relative_bearing_deg']):.1f} deg | "
                f"delta {float(best['delta_db']):.1f} dB | z {float(best['z_score']):.1f}"
            )
        else:
            self.candidate_text.set(
                f"Detection: {data.get('reason', 'waiting')} | baseline samples {data.get('baseline_samples', 0)}"
            )

    def open_preview(self) -> None:
        if self.preview_window is not None:
            self.preview_window.lift()
            self.preview_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        window.title("Camera Preview")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.close_preview)
        self.preview_window = window

        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        image_frame = ttk.Frame(frame, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1])
        image_frame.pack()
        image_frame.pack_propagate(False)
        self.preview_label = ttk.Label(image_frame, text="Waiting for camera...", anchor="center")
        self.preview_label.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.preview_status, anchor="w").pack(fill="x", pady=(8, 0))

        self.preview_data = None
        self.preview_frame_number = 0
        self.displayed_frame_number = 0
        camera_topic = str(self.get_parameter("camera_topic").value)
        self.preview_subscription = self.create_subscription(
            CompressedImage,
            camera_topic,
            self.camera_callback,
            qos_profile_sensor_data,
        )
        self.preview_status.set(f"Waiting for {camera_topic}")
        window.after(100, self.refresh_preview)

    def close_preview(self) -> None:
        if self.preview_subscription is not None:
            self.destroy_subscription(self.preview_subscription)
            self.preview_subscription = None
        if self.preview_window is not None:
            self.preview_window.destroy()
        self.preview_window = None
        self.preview_label = None
        self.preview_data = None
        self.preview_photo = None

    def camera_callback(self, message: CompressedImage) -> None:
        self.preview_data = bytes(message.data)
        self.preview_frame_number += 1

    def refresh_preview(self) -> None:
        if self.preview_window is None or self.preview_label is None:
            return
        if self.preview_data is not None and self.preview_frame_number != self.displayed_frame_number:
            try:
                image = decode_preview(self.preview_data, PREVIEW_SIZE)
                self.preview_photo = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=self.preview_photo, text="")
                self.preview_status.set(f"{image.width} x {image.height} | frame {self.preview_frame_number}")
                self.displayed_frame_number = self.preview_frame_number
            except (OSError, UnidentifiedImageError) as error:
                self.preview_status.set(f"Decode error: {error}")
        self.preview_window.after(100, self.refresh_preview)

    @staticmethod
    def decode(message: String) -> dict:
        try:
            return json.loads(message.data)
        except json.JSONDecodeError:
            return {}

    def spin_once(self) -> None:
        if rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.0)
            self.root.after(20, self.spin_once)

    def close(self) -> None:
        self.close_preview()
        self.root.quit()

    def run(self) -> None:
        self.root.after(20, self.spin_once)
        self.root.mainloop()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OperatorGui()
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()