import ipaddress
import math
import struct

import zmq


def orientation_endpoint(address: str, port: str) -> str:
    try:
        host = ipaddress.ip_address(address.strip())
    except ValueError as error:
        raise ValueError("Enter a valid IP address") from error
    try:
        port_number = int(port)
    except ValueError as error:
        raise ValueError("Port must be an integer from 1 to 65535") from error
    if not 1 <= port_number <= 65535:
        raise ValueError("Port must be an integer from 1 to 65535")
    host_text = f"[{host}]" if host.version == 6 else str(host)
    return f"tcp://{host_text}:{port_number}"


def send_orientation(endpoint: str, heading: float, timeout_ms: int = 1500) -> None:
    if not math.isfinite(heading):
        raise ValueError("Compass orientation must be finite")
    payload = struct.pack("<f", heading)
    with zmq.Context() as context:
        with context.socket(zmq.PUSH) as sender:
            sender.setsockopt(zmq.IPV6, 1)
            sender.setsockopt(zmq.IMMEDIATE, 1)
            sender.setsockopt(zmq.SNDTIMEO, timeout_ms)
            sender.setsockopt(zmq.LINGER, timeout_ms)
            sender.connect(endpoint)
            sender.send(payload)