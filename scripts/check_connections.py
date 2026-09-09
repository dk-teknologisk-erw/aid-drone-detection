#!/usr/bin/env python3

import fcntl
import glob
import os
import re
import socket
import subprocess
import sys
from pathlib import Path


DEVICES = {
    "MCU": {
        "pattern": "/dev/serial/by-id/usb-Raspberry_Pi_Pico_2_*-if00",
        "usb_id": "2e8a:000f",
    },
    "RF Explorer": {
        "pattern": "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_*-if00-port0",
        "usb_id": "10c4:ea60",
    },
}
I2C_SLAVE = 0x0703


def command(*arguments: str) -> tuple[int, str]:
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip()


def usb_paths_and_disconnects() -> dict[str, str]:
    _, journal = command("journalctl", "-k", "-b", "--no-pager", "-o", "short-iso")
    path_ids: dict[str, str] = {}
    disconnects: dict[str, str] = {}
    pending_path = ""
    for line in journal.splitlines():
        match = re.search(r"kernel: usb ([0-9]+-[0-9.]+):", line)
        if match:
            pending_path = match.group(1)
        identity = re.search(r"idVendor=([0-9a-f]{4}), idProduct=([0-9a-f]{4})", line)
        if identity and pending_path:
            path_ids[pending_path] = f"{identity.group(1)}:{identity.group(2)}"
        disconnected = re.search(r"kernel: usb ([0-9]+-[0-9.]+): USB disconnect", line)
        if disconnected:
            usb_id = path_ids.get(disconnected.group(1))
            if usb_id:
                disconnects[usb_id] = line.split(" ", 1)[0]
    return disconnects


def check_serial_devices(disconnects: dict[str, str]) -> bool:
    healthy = True
    for name, device in DEVICES.items():
        matches = glob.glob(device["pattern"])
        connected = len(matches) == 1 and Path(matches[0]).resolve().exists()
        if connected:
            target = Path(matches[0]).resolve()
            accessible = os.access(target, os.R_OK | os.W_OK)
            detail = f"connected at {target} ({'accessible' if accessible else 'permission denied'})"
            healthy &= accessible
        elif matches:
            detail = "stale device link"
            healthy = False
        else:
            detail = "not connected"
            healthy = False
        last_disconnect = disconnects.get(device["usb_id"], "none recorded this boot")
        print(f"{'OK' if connected else 'FAIL':4}  {name:11} {detail}; last disconnect: {last_disconnect}")
    return healthy


def check_compass() -> bool:
    try:
        descriptor = os.open("/dev/i2c-1", os.O_RDWR)
        try:
            fcntl.ioctl(descriptor, I2C_SLAVE, 0x2C)
            os.write(descriptor, b"\x00")
            chip_id = os.read(descriptor, 1)[0]
        finally:
            os.close(descriptor)
        if chip_id != 0x80:
            raise RuntimeError(f"unexpected chip ID 0x{chip_id:02x}")
        print("OK    Compass     QMC5883P at /dev/i2c-1 address 0x2c")
        return True
    except (OSError, RuntimeError) as error:
        print(f"FAIL  Compass     {error}")
        return False


def check_zenoh() -> bool:
    active, _ = command("systemctl", "is-active", "--quiet", "zenoh-bridge.service")
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", 7447), timeout=1.0):
            listening = True
    except OSError:
        pass
    if active == 0 and listening:
        print("OK    Zenoh       service active; listening on TCP 7447")
        return True
    print(
        "FAIL  Zenoh       "
        f"service {'active' if active == 0 else 'inactive'}; "
        f"TCP 7447 {'listening' if listening else 'closed'}"
    )
    return False


def print_network_addresses() -> None:
    _, output = command("ip", "-brief", "-4", "address", "show", "up")
    addresses = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] != "lo":
            addresses.append(f"{fields[0]}={fields[2].split('/')[0]}")
    print(f"INFO  Network     {', '.join(addresses) if addresses else 'no active interface'}")


def main() -> int:
    disconnects = usb_paths_and_disconnects()
    healthy = check_serial_devices(disconnects)
    healthy &= check_compass()
    healthy &= check_zenoh()
    print_network_addresses()
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())