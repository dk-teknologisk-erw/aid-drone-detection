import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .rf_source import RfExplorerSource, ScanConfig, scan_config
from .ros_utils import run_node


def find_rf_port() -> str:
    matches = list(Path("/dev/serial/by-id").glob("usb-Silicon_Labs_CP2102N_*-if00-port0"))
    return str(matches[0]) if len(matches) == 1 else "/dev/ttyUSB0"


class RfSweepNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_rf_sweep")
        self.declare_parameter("port", find_rf_port())
        self.declare_parameter("baudrate", 500000)
        self.declare_parameter("timeout", 10.0)
        self.declare_parameter("initial_band", "2.4")
        self.sweep_publisher = self.create_publisher(String, "/aid/rf/sweep", 10)
        self.status_publisher = self.create_publisher(String, "/aid/rf/status", 10)
        self.last_status = {"connected": False, "state": "starting"}
        self.create_subscription(String, "/aid/rf/config", self.config_callback, 10)
        self.config_lock = threading.Lock()
        self.desired_config = scan_config(str(self.get_parameter("initial_band").value))
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()
        self.create_timer(1.0, self.republish_status)

    def config_callback(self, message: String) -> None:
        try:
            request = json.loads(message.data)
            config = scan_config(
                str(request.get("selection", "")),
                float(request.get("center_mhz", 0.0)),
                float(request.get("span_mhz", 20.0)),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"Invalid RF configuration: {error}")
            return
        with self.config_lock:
            self.desired_config = config

    def get_desired_config(self) -> ScanConfig:
        with self.config_lock:
            return self.desired_config

    def publish_status(self, **status) -> None:
        self.last_status = status
        if rclpy.ok() and not self.stop_event.is_set():
            self.status_publisher.publish(String(data=json.dumps(status, separators=(",", ":"))))

    def republish_status(self) -> None:
        if rclpy.ok() and not self.stop_event.is_set():
            self.status_publisher.publish(String(data=json.dumps(self.last_status, separators=(",", ":"))))

    def run(self) -> None:
        sequence = 0
        port = str(self.get_parameter("port").value)
        last_warning_time = 0.0
        while not self.stop_event.is_set():
            if not Path(port).exists():
                error = f"RF Explorer unavailable: {port} does not exist"
                self.publish_status(connected=False, state="reconnecting", error=error)
                if time.monotonic() - last_warning_time >= 30.0:
                    self.get_logger().warning(error)
                    last_warning_time = time.monotonic()
                self.stop_event.wait(2.0)
                continue
            source = RfExplorerSource(
                port,
                int(self.get_parameter("baudrate").value),
                float(self.get_parameter("timeout").value),
            )
            active_config: ScanConfig | None = None
            try:
                source.connect()
                self.publish_status(connected=True, state="connected")
                while not self.stop_event.is_set():
                    requested = self.get_desired_config()
                    if requested != active_config:
                        source.configure(requested)
                        source.next_sweep()
                        active_config = requested
                        self.publish_status(
                            connected=True,
                            state="scanning",
                            selection=active_config.name,
                            start_mhz=active_config.start_mhz,
                            stop_mhz=active_config.stop_mhz,
                        )
                    sweep = source.next_sweep()
                    sequence += 1
                    record = {
                        "schema": "aid.rf_sweep.v1",
                        "sequence": sequence,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "band": active_config.name,
                        "module": source.analyzer.ActiveModel.name,
                        "start_mhz": sweep.StartFrequencyMHZ,
                        "stop_mhz": sweep.EndFrequencyMHZ,
                        "step_mhz": sweep.StepFrequencyMHZ,
                        "amplitudes_dbm": [sweep.GetAmplitude_DBM(index) for index in range(sweep.TotalDataPoints)],
                    }
                    if rclpy.ok() and not self.stop_event.is_set():
                        self.sweep_publisher.publish(String(data=json.dumps(record, separators=(",", ":"))))
            except Exception as error:
                if not rclpy.ok() or self.stop_event.is_set():
                    break
                self.publish_status(connected=False, state="reconnecting", error=str(error))
                if time.monotonic() - last_warning_time >= 30.0:
                    self.get_logger().warning(f"RF Explorer unavailable: {error}")
                    last_warning_time = time.monotonic()
            finally:
                source.close()
            self.stop_event.wait(2.0)

    def destroy_node(self) -> None:
        self.stop_event.set()
        self.worker.join(timeout=2.0)
        super().destroy_node()


def main(args=None) -> None:
    run_node(RfSweepNode, args)


if __name__ == "__main__":
    main()