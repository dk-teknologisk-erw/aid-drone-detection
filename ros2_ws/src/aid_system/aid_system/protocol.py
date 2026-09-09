import json
from typing import Any


VALID_MOTOR_MODES = {"stop", "explore", "focus"}


def encode_command(command: str, **values: Any) -> bytes:
    return (json.dumps({"cmd": command, **values}, separators=(",", ":")) + "\n").encode()


def decode_status(line: bytes) -> dict[str, Any]:
    message = json.loads(line.decode("utf-8"))
    if message.get("type") != "status":
        raise ValueError("not an MCU status message")
    return message