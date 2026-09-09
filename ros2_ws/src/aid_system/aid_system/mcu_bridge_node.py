import json
import time
from collections import deque
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.node import Node
import serial
from serial import SerialException
from std_msgs.msg import Float32, String

from .protocol import VALID_MOTOR_MODES, decode_status, encode_command
from .ros_utils import run_node


def find_pico_port() -> str:
    matches = list(Path("/dev/serial/by-id").glob("usb-Raspberry_Pi_Pico_2_*-if00"))
    return str(matches[0]) if len(matches) == 1 else "/dev/ttyACM0"


class McuBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_mcu_bridge")
        self.declare_parameter("port", find_pico_port())
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("reconnect_seconds", 2.0)

        self.port = str(self.get_parameter("port").value)
        self.baudrate = int(self.get_parameter("baudrate").value)
        self.reconnect_seconds = float(self.get_parameter("reconnect_seconds").value)
        self.serial_port: Optional[serial.Serial] = None
        self.receive_buffer = bytearray()
        self.transmit_queue: deque[bytes] = deque()
        self.last_connect_attempt = self.get_clock().now()
        self.mode = "stop"
        self.speed_dps = 30.0
        self.target_deg = 0.0
        self.last_heartbeat = 0.0

        self.angle_publisher = self.create_publisher(Float32, "/aid/motor/angle_deg", 20)
        self.status_publisher = self.create_publisher(String, "/aid/motor/status", 20)
        self.create_subscription(String, "/aid/motor/mode", self.mode_callback, 10)
        self.create_subscription(Float32, "/aid/motor/speed_dps", self.speed_callback, 10)
        self.create_subscription(Float32, "/aid/motor/target_deg", self.target_callback, 10)
        self.create_subscription(Float32, "/aid/motor/zero_deg", self.zero_callback, 10)
        self.create_timer(0.02, self.io_tick)

    def mode_callback(self, message: String) -> None:
        if message.data not in VALID_MOTOR_MODES:
            self.get_logger().warning(f"Ignoring invalid motor mode: {message.data}")
            return
        self.mode = message.data
        self.transmit_queue.append(encode_command("mode", mode=self.mode))

    def speed_callback(self, message: Float32) -> None:
        self.speed_dps = max(0.1, min(90.0, float(message.data)))
        self.transmit_queue.append(encode_command("speed", speed_dps=self.speed_dps))

    def target_callback(self, message: Float32) -> None:
        self.target_deg = float(message.data) % 360.0
        self.transmit_queue.append(encode_command("target", angle_deg=self.target_deg))
        self.mode = "focus"
        self.transmit_queue.append(encode_command("mode", mode=self.mode))

    def zero_callback(self, message: Float32) -> None:
        self.transmit_queue.append(encode_command("zero", angle_deg=float(message.data) % 360.0))

    def connect(self) -> None:
        self.serial_port = serial.Serial(
            self.port,
            self.baudrate,
            timeout=0,
            write_timeout=0.2,
            exclusive=True,
        )
        self.receive_buffer.clear()
        self.last_heartbeat = 0.0
        self.transmit_queue.extendleft(
            reversed(
                [
                    encode_command("speed", speed_dps=self.speed_dps),
                    encode_command("target", angle_deg=self.target_deg),
                    encode_command("mode", mode=self.mode),
                ]
            )
        )
        self.get_logger().info(f"Connected to Pico on {self.port}")

    def disconnect(self, error: Exception) -> None:
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except SerialException:
                pass
        self.serial_port = None
        self.status_publisher.publish(String(data=json.dumps({"connected": False, "error": str(error)})))
        self.get_logger().warning(f"Pico disconnected: {error}")

    def io_tick(self) -> None:
        if self.serial_port is None:
            now = self.get_clock().now()
            if (now - self.last_connect_attempt).nanoseconds < self.reconnect_seconds * 1e9:
                return
            self.last_connect_attempt = now
            try:
                self.connect()
            except (OSError, SerialException) as error:
                self.get_logger().warning(f"Pico connection failed: {error}")
            return

        try:
            now = time.monotonic()
            if now - self.last_heartbeat >= 0.5:
                self.transmit_queue.append(encode_command("ping"))
                self.last_heartbeat = now
            while self.transmit_queue:
                self.serial_port.write(self.transmit_queue.popleft())
            waiting = self.serial_port.in_waiting
            if waiting:
                self.receive_buffer.extend(self.serial_port.read(waiting))
            while b"\n" in self.receive_buffer:
                line, _, remainder = self.receive_buffer.partition(b"\n")
                self.receive_buffer = bytearray(remainder)
                if line:
                    self.publish_status(decode_status(line))
        except (OSError, SerialException, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            if isinstance(error, (OSError, SerialException)):
                self.disconnect(error)
            else:
                self.get_logger().warning(f"Invalid Pico message: {error}")

    def publish_status(self, status: dict) -> None:
        status["connected"] = True
        self.status_publisher.publish(String(data=json.dumps(status, separators=(",", ":"))))
        if "angle_deg" in status:
            self.angle_publisher.publish(Float32(data=float(status["angle_deg"])))

    def destroy_node(self) -> None:
        if self.serial_port is not None:
            try:
                self.serial_port.write(encode_command("stop"))
                self.serial_port.flush()
            except (OSError, SerialException):
                pass
            self.serial_port.close()
        super().destroy_node()


def main(args=None) -> None:
    run_node(McuBridgeNode, args)


if __name__ == "__main__":
    main()