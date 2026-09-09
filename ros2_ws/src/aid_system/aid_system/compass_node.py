import fcntl
import json
import math
import os
import struct
import time
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from std_msgs.msg import Empty, Float32, String

from .ros_utils import run_node


I2C_SLAVE = 0x0703


def normalize_signed(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


class QMC5883P:
    def __init__(self, bus: int, address: int) -> None:
        self.file_descriptor = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        fcntl.ioctl(self.file_descriptor, I2C_SLAVE, address)
        if self.read(0x00, 1)[0] != 0x80:
            raise RuntimeError("QMC5883P chip ID mismatch")
        self.write(0x0B, 0x08)
        self.write(0x0A, 0x55)
        time.sleep(0.05)

    def read(self, register: int, length: int) -> bytes:
        os.write(self.file_descriptor, bytes([register]))
        return os.read(self.file_descriptor, length)

    def write(self, register: int, value: int) -> None:
        os.write(self.file_descriptor, bytes([register, value]))

    def axes(self) -> tuple[int, int, int]:
        if not self.read(0x09, 1)[0] & 0x01:
            raise BlockingIOError("compass sample not ready")
        return struct.unpack("<hhh", self.read(0x01, 6))

    def close(self) -> None:
        os.close(self.file_descriptor)


class CompassNode(Node):
    def __init__(self) -> None:
        super().__init__("aid_compass")
        self.declare_parameter("bus", 1)
        self.declare_parameter("address", 0x2C)
        self.declare_parameter("publish_hz", 10.0)
        self.sensor = QMC5883P(
            int(self.get_parameter("bus").value),
            int(self.get_parameter("address").value),
        )
        self.reference_heading: float | None = None
        self.latest_heading: float | None = None
        self.heading_publisher = self.create_publisher(Float32, "/aid/compass/relative_heading_deg", 20)
        self.status_publisher = self.create_publisher(String, "/aid/compass/status", 20)
        self.create_subscription(Empty, "/aid/compass/zero", self.zero_callback, 10)
        self.create_timer(1.0 / float(self.get_parameter("publish_hz").value), self.sample)

    def zero_callback(self, _message: Empty) -> None:
        if self.latest_heading is not None:
            self.reference_heading = self.latest_heading

    def sample(self) -> None:
        try:
            raw_x, raw_y, raw_z = self.sensor.axes()
        except BlockingIOError:
            return
        except OSError as error:
            self.get_logger().error(f"Compass read failed: {error}")
            return

        scale = 100.0 / 3750.0
        x_ut, y_ut, z_ut = raw_x * scale, raw_y * scale, raw_z * scale
        self.latest_heading = math.degrees(math.atan2(y_ut, x_ut)) % 360.0
        if self.reference_heading is None:
            self.reference_heading = self.latest_heading
        relative = normalize_signed(self.latest_heading - self.reference_heading)
        self.heading_publisher.publish(Float32(data=relative))
        status = {
            "schema": "aid.compass.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "relative_heading_deg": round(relative, 3),
            "magnetic_ut": {"x": round(x_ut, 3), "y": round(y_ut, 3), "z": round(z_ut, 3)},
        }
        self.status_publisher.publish(String(data=json.dumps(status, separators=(",", ":"))))

    def destroy_node(self) -> None:
        self.sensor.close()
        super().destroy_node()


def main(args=None) -> None:
    run_node(CompassNode, args)


if __name__ == "__main__":
    main()