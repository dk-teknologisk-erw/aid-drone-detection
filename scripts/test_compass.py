#!/usr/bin/env python3

import argparse
import fcntl
import json
import math
import os
import struct
import sys
import time
from datetime import datetime, timezone


I2C_SLAVE = 0x0703
QMC5883P_ADDRESS = 0x2C
CHIP_ID_REGISTER = 0x00
EXPECTED_CHIP_ID = 0x80
DATA_REGISTER = 0x01
STATUS_REGISTER = 0x09
CONTROL_1_REGISTER = 0x0A
CONTROL_2_REGISTER = 0x0B
LSB_PER_GAUSS = 3750.0


class QMC5883P:
    def __init__(self, bus_number: int, address: int) -> None:
        self.path = f"/dev/i2c-{bus_number}"
        self.address = address
        self.file_descriptor = os.open(self.path, os.O_RDWR)
        fcntl.ioctl(self.file_descriptor, I2C_SLAVE, address)

    def close(self) -> None:
        os.close(self.file_descriptor)

    def read_register(self, register: int, length: int = 1) -> bytes:
        os.write(self.file_descriptor, bytes([register]))
        return os.read(self.file_descriptor, length)

    def write_register(self, register: int, value: int) -> None:
        os.write(self.file_descriptor, bytes([register, value]))

    def initialize(self) -> None:
        chip_id = self.read_register(CHIP_ID_REGISTER)[0]
        if chip_id != EXPECTED_CHIP_ID:
            raise RuntimeError(
                f"unexpected chip ID 0x{chip_id:02X}; expected 0x{EXPECTED_CHIP_ID:02X}"
            )

        self.write_register(CONTROL_2_REGISTER, 0x08)
        self.write_register(CONTROL_1_REGISTER, 0x55)
        time.sleep(0.05)

    def read_axes(self, timeout: float = 0.5) -> tuple[int, int, int]:
        deadline = time.monotonic() + timeout
        while not self.read_register(STATUS_REGISTER)[0] & 0x01:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for compass data")
            time.sleep(0.001)

        return struct.unpack("<hhh", self.read_register(DATA_REGISTER, 6))


def parse_address(value: str) -> int:
    address = int(value, 0)
    if not 0x03 <= address <= 0x77:
        raise argparse.ArgumentTypeError("address must be between 0x03 and 0x77")
    return address


def main() -> int:
    parser = argparse.ArgumentParser(description="Test a QMC5883P compass over I2C")
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument(
        "--address",
        type=parse_address,
        default=QMC5883P_ADDRESS,
        help="I2C address (default: 0x2C)",
    )
    parser.add_argument("--samples", type=int, default=10, help="number of readings")
    parser.add_argument(
        "--interval", type=float, default=0.2, help="seconds between readings"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON Lines")
    arguments = parser.parse_args()
    if arguments.samples < 0:
        parser.error("--samples must be 0 or greater; 0 runs until Ctrl+C")
    if arguments.interval < 0:
        parser.error("--interval must be non-negative")

    compass = QMC5883P(arguments.bus, arguments.address)
    try:
        compass.initialize()
        if not arguments.json:
            print(
                f"QMC5883P detected on {compass.path} at 0x{compass.address:02X}"
            )
            print(" sample    raw_x    raw_y    raw_z       x_uT       y_uT       z_uT  heading")
        sample = 1
        while arguments.samples == 0 or sample <= arguments.samples:
            raw_x, raw_y, raw_z = compass.read_axes()
            scale = 100.0 / LSB_PER_GAUSS
            x_microtesla = raw_x * scale
            y_microtesla = raw_y * scale
            z_microtesla = raw_z * scale
            heading = math.degrees(math.atan2(y_microtesla, x_microtesla)) % 360.0
            if arguments.json:
                print(
                    json.dumps(
                        {
                            "schema": "aid.compass.v1",
                            "timestamp_utc": datetime.now(timezone.utc)
                            .isoformat(timespec="milliseconds")
                            .replace("+00:00", "Z"),
                            "sample": sample,
                            "raw": {"x": raw_x, "y": raw_y, "z": raw_z},
                            "microtesla": {
                                "x": round(x_microtesla, 3),
                                "y": round(y_microtesla, 3),
                                "z": round(z_microtesla, 3),
                            },
                            "heading_uncalibrated_deg": round(heading, 3),
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            else:
                print(
                    f"{sample:7d} {raw_x:8d} {raw_y:8d} {raw_z:8d} "
                    f"{x_microtesla:10.2f} {y_microtesla:10.2f} "
                    f"{z_microtesla:10.2f} {heading:8.1f}"
                )
            sample += 1
            time.sleep(arguments.interval)
    finally:
        compass.close()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"Compass test failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error