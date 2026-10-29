import json
import math
import struct
import time
import tkinter as tk
from tkinter import ttk

import zmq

from .follower_cues import coordinates, finite_number
from .orientation_sender import orientation_endpoint


def simulated_status(latitude: str, longitude: str, altitude: str, north: str, east: str) -> dict:
    gps_available = bool(latitude.strip() or longitude.strip())
    lat, lon = coordinates(latitude, longitude) if gps_available else (None, None)
    height = finite_number(altitude, "Simulated altitude")
    return {
        "status": True, "test_only": True, "state": "TEST RECEIVER", "mode": "SIMULATION",
        "armed": False, "gps_ok": gps_available, "pos_ok": True, "lat": lat, "lon": lon,
        "alt_rel_m": height,
        "pos_ned": [
            finite_number(north, "Simulated north"),
            finite_number(east, "Simulated east"), -height,
        ],
    }


def cue_reply(message: object, kind: str, status: dict) -> dict:
    if not isinstance(message, dict):
        raise ValueError("JSON request must be an object")
    if message.get("cmd") == "status":
        return status.copy()
    if message.get("cmd") == "clear":
        pass
    elif "cmd" in message:
        raise ValueError("Unknown command")
    elif kind == "position":
        coordinates(message.get("lat"), message.get("lon"))
        finite_number(message.get("alt_m"), "Altitude")
        if message.get("alt_ref") not in {"rel", "amsl"}:
            raise ValueError("alt_ref must be rel or amsl")
    else:
        for key in ("n", "e", "d"):
            finite_number(message.get(key), key)
    return {
        "ok": True, "test_only": True,
        "warning": "TEST RECEIVER ONLY: message received; no flight action or authentication check",
    }


class TestReceiver:
    __test__ = False

    def __init__(self) -> None:
        self.context = zmq.Context()
        self.sockets: dict[str, zmq.Socket] = {}

    def start(self, host: str, position_port: str, search_port: str, compass_port: str) -> None:
        if self.sockets:
            raise ValueError("Stop the receiver before changing ports")
        endpoints = [
            orientation_endpoint(host, port)
            for port in (position_port, search_port, compass_port)
        ]
        if len(set(endpoints)) != 3:
            raise ValueError("Position, search and compass ports must be different")
        try:
            for (kind, socket_type), endpoint in zip(
                (("position", zmq.REP), ("search", zmq.REP), ("compass", zmq.PULL)),
                endpoints,
            ):
                socket = self.context.socket(socket_type)
                self.sockets[kind] = socket
                socket.setsockopt(zmq.IPV6, 1)
                socket.setsockopt(zmq.LINGER, 0)
                socket.bind(endpoint)
        except zmq.ZMQError:
            self.stop()
            raise

    def receive(self, status: dict) -> list[str]:
        records = []
        for kind, socket in self.sockets.items():
            for _ in range(30):
                if not socket.poll(0, zmq.POLLIN):
                    break
                frames = socket.recv_multipart(flags=zmq.NOBLOCK)
                payload = frames[0] if len(frames) == 1 else b""
                if kind == "compass":
                    if len(frames) == 1 and len(payload) == 4:
                        heading = struct.unpack("<f", payload)[0]
                        if math.isfinite(heading):
                            records.append(f"COMPASS: {heading:+.3f} deg (little-endian Float32)")
                            continue
                    records.append(f"COMPASS INVALID: expected one finite Float32; received {frames!r}")
                    continue
                try:
                    if len(frames) != 1:
                        raise ValueError("Expected one JSON frame")
                    message = json.loads(payload)
                    reply = cue_reply(message, kind, status)
                except (ValueError, TypeError) as error:
                    reply = {"ok": False, "test_only": True, "error": str(error)}
                records.append(
                    f"{kind.upper()} RX: {payload.decode('utf-8', errors='replace')}\n"
                    f"SIMULATED REPLY: {json.dumps(reply)}"
                )
                socket.send_json(reply, flags=zmq.NOBLOCK)
        return records

    def stop(self) -> None:
        for socket in self.sockets.values():
            socket.close(linger=0)
        self.sockets.clear()

    def close(self) -> None:
        self.stop()
        self.context.term()


class ReceiverGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.receiver = TestReceiver()
        self.after_id: str | None = None
        root.title("AID ZeroMQ test receiver - NO FLIGHT CONTROL")
        root.geometry("820x650")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.host = tk.StringVar(value="127.0.0.1")
        self.position_port = tk.StringVar(value="5558")
        self.search_port = tk.StringVar(value="5559")
        self.compass_port = tk.StringVar(value="5555")
        self.latitude = tk.StringVar()
        self.longitude = tk.StringVar()
        self.altitude = tk.StringVar(value="0")
        self.north = tk.StringVar(value="0")
        self.east = tk.StringVar(value="0")
        self.status_text = tk.StringVar(value="Stopped")
        self.count = 0
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="TEST ONLY: displays received messages and simulates replies. "
                        "Never controls a drone. Signatures and flight limits are NOT verified.",
            wraplength=780, justify="left",
        ).pack(fill="x")
        network = ttk.LabelFrame(frame, text="Listen endpoints", padding=8)
        network.pack(fill="x", pady=8)
        self.endpoint_entries = []
        for row, (label, variable) in enumerate((
            ("Bind IP (0.0.0.0 for other computers)", self.host),
            ("Position JSON / REP port", self.position_port),
            ("Search N/E/D JSON / REP port", self.search_port),
            ("Compass binary Float32 / PULL port", self.compass_port),
        )):
            ttk.Label(network, text=label).grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(network, textvariable=variable, width=20)
            entry.grid(row=row, column=1, padx=8)
            self.endpoint_entries.append(entry)
        simulation = ttk.LabelFrame(frame, text="Simulated follower status (not measured)", padding=8)
        simulation.pack(fill="x")
        ttk.Label(
            simulation, text="Leave GPS blank only when the sender explicitly assumes follower at tower.",
            wraplength=760,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        for index, (label, variable) in enumerate((
            ("Latitude", self.latitude), ("Longitude", self.longitude),
            ("Altitude above home (m)", self.altitude),
            ("North from home (m)", self.north), ("East from home (m)", self.east),
        )):
            ttk.Label(simulation, text=label).grid(row=1 + index // 2, column=(index % 2) * 2, sticky="w")
            ttk.Entry(simulation, textvariable=variable, width=16).grid(
                row=1 + index // 2, column=(index % 2) * 2 + 1, padx=8, pady=2,
            )
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=8)
        self.start_button = ttk.Button(controls, text="Start listening", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop).pack(side="left", padx=8)
        ttk.Button(controls, text="Clear log", command=self.clear_log).pack(side="left")
        ttk.Label(frame, textvariable=self.status_text).pack(anchor="w")
        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(log_frame, state="disabled", wrap="word", height=12)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} {text}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 2000:
            self.log.delete("1.0", "501.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.receiver.sockets:
            return
        try:
            self.receiver.start(
                self.host.get(), self.position_port.get(), self.search_port.get(),
                self.compass_port.get(),
            )
        except (ValueError, zmq.ZMQError) as error:
            self.status_text.set(f"Cannot listen: {error}")
            return
        self.count = 0
        self.start_button.configure(state="disabled")
        for entry in self.endpoint_entries:
            entry.configure(state="disabled")
        self.append("Listeners started. Point the sender at this computer's IP and matching port.")
        self.poll()

    def poll(self) -> None:
        self.after_id = None
        try:
            try:
                status = simulated_status(
                    self.latitude.get(), self.longitude.get(), self.altitude.get(),
                    self.north.get(), self.east.get(),
                )
            except ValueError as error:
                status = {"ok": False, "test_only": True, "error": f"Set simulated GPS/status: {error}"}
            records = self.receiver.receive(status)
            for record in records:
                self.count += 1
                self.append(record)
            self.status_text.set(f"Listening - received {self.count} messages (simulation only)")
        except zmq.ZMQError as error:
            self.stop()
            self.status_text.set(f"Listener failed: {error}")
            return
        self.after_id = self.root.after(50, self.poll)

    def stop(self) -> None:
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.receiver.stop()
        self.start_button.configure(state="normal")
        for entry in self.endpoint_entries:
            entry.configure(state="normal")
        self.status_text.set(f"Stopped - received {self.count} messages")

    def close(self) -> None:
        self.stop()
        self.receiver.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ReceiverGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
