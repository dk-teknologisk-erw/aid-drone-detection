from concurrent.futures import Future, ThreadPoolExecutor
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import zmq

from .follower_cues import (
    coordinates, estimated_position, finite_number, follower_coordinates, request_cue,
    validate_cue_against_status, validate_follower_status,
)
from .orientation_sender import orientation_endpoint


class FollowerPanel:
    def __init__(self, parent: ttk.Frame, context: Callable[[], float]) -> None:
        self.context = context
        self.frame = ttk.LabelFrame(parent, text="Follower: estimated target position", padding=10)
        self.frame.pack(fill="x", pady=(10, 0))
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="5558")
        self.secret = tk.StringVar()
        self.latitude = tk.StringVar()
        self.longitude = tk.StringVar()
        self.distance = tk.StringVar(value="100")
        self.altitude = tk.StringVar(value="30")
        self.correction = tk.StringVar(value="0")
        self.follower_at_tower = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Not checked. No cues are sent automatically.")
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future: Future[dict] | None = None
        self.after_id: str | None = None
        self.status: dict | None = None
        self.status_at = 0.0
        self.request_identity: tuple[str, str, str] | None = None
        self.input_revision = 0
        self.request_revision = 0
        self.closed = False
        fields = (
            ("Follower IP", self.host), ("Position port", self.port),
            ("Secret (optional)", self.secret), ("Tower latitude", self.latitude),
            ("Tower longitude", self.longitude), ("Horizontal distance (m)", self.distance),
            ("Altitude above follower home (m)", self.altitude),
            ("Heading correction (deg; optional, default 0)", self.correction),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(
                self.frame, textvariable=variable, width=24,
                show="*" if variable is self.secret else "",
            ).grid(row=row, column=1, sticky="ew", padx=8, pady=2)
        self.frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            self.frame, text="Assume follower is CURRENTLY at tower (latitude/longitude)",
            variable=self.follower_at_tower,
        ).grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(
            self.frame,
            text="Uses the current compass direction. Magnetic north may differ from true "
                 "north; with no correction, the estimated GPS point can be offset.",
            wraplength=690, justify="left",
        ).grid(row=9, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=10, column=0, columnspan=2, sticky="w", pady=6)
        self.buttons = [
            ttk.Button(buttons, text="Check follower", command=self.check),
            ttk.Button(buttons, text="Preview / Send position", command=self.send_position),
            ttk.Button(buttons, text="Clear position cue", command=self.clear),
        ]
        for button in self.buttons:
            button.pack(side="left", padx=4)
        ttk.Label(
            self.frame, textvariable=self.status_text, wraplength=690, justify="left",
        ).grid(row=11, column=0, columnspan=2, sticky="w")
        for variable in (
            self.host, self.port, self.secret, self.latitude, self.longitude, self.follower_at_tower,
        ):
            variable.trace_add("write", self.invalidate_status)

    def invalidate_status(self, *_args) -> None:
        self.status = None
        self.status_at = 0.0
        self.input_revision += 1
        self.status_text.set("Destination or position inputs changed: check follower again.")

    def identity(self) -> tuple[str, str, str]:
        host, port = self.host.get().strip(), self.port.get().strip()
        orientation_endpoint(host, port)
        return host, port, self.secret.get()

    def assumed_position(self) -> tuple[float, float] | None:
        if self.follower_at_tower.get():
            return coordinates(self.latitude.get(), self.longitude.get())
        return None

    def start_request(self, message: dict) -> None:
        if self.future is not None:
            self.status_text.set("A follower request is already in progress.")
            return
        try:
            host, port, secret = self.identity()
            if message.get("cmd") == "status":
                self.assumed_position()
        except ValueError as error:
            self.status_text.set(str(error))
            return
        self.request_identity = (host, port, secret)
        self.request_revision = self.input_revision
        self.future = self.executor.submit(request_cue, host, port, message, secret)
        self.status_text.set("Checking follower..." if message.get("cmd") == "status"
                             else "Awaiting acknowledgement; do not resend.")
        for button in self.buttons:
            button.configure(state="disabled")
        self.after_id = self.frame.after(50, lambda: self.poll(message))

    def check(self) -> None:
        self.status = None
        self.start_request({"cmd": "status"})

    def poll(self, message: dict) -> None:
        self.after_id = None
        if self.closed or self.future is None:
            return
        if not self.future.done():
            self.after_id = self.frame.after(50, lambda: self.poll(message))
            return
        try:
            reply = self.future.result()
            if self.request_identity != self.identity():
                raise ValueError("Destination changed during request; result not applied.")
            if self.request_revision != self.input_revision:
                raise ValueError("Position inputs changed during request; check follower again.")
            if message.get("cmd") == "status":
                assumed = self.assumed_position()
                self.status = validate_follower_status(reply, assumed)
                self.status_at = time.monotonic()
                lat, lon = follower_coordinates(reply, assumed)
                position_label = (
                    "ASSUMED AT TOWER (not measured GPS)"
                    if assumed is not None else "Reported GPS"
                )
                self.status_text.set(
                    f"Follower: {reply.get('state', '?')} | {reply.get('mode', '?')} | "
                    f"{'ARMED' if reply.get('armed') else 'disarmed'} | "
                    f"{position_label} {lat:.6f}, {lon:.6f} | "
                    f"{float(reply['alt_rel_m']):.1f} m above home. Status valid for 10s."
                )
            elif reply.get("ok") is True:
                self.status_text.set(
                    "ACCEPTED by follower (not confirmation of flight completion)."
                    + (f" Warning: {reply['warning']}" if reply.get("warning") else "")
                )
            elif reply.get("ok") is False:
                self.status_text.set(f"REJECTED: {reply.get('error', 'unspecified reason')}")
            else:
                raise ValueError("Invalid acknowledgement; outcome unknown. Check follower.")
        except (TimeoutError, ValueError, TypeError, KeyError, zmq.ZMQError) as error:
            self.status = None
            self.status_text.set(f"Follower request failed: {error}")
        finally:
            self.future = None
            for button in self.buttons:
                button.configure(state="normal")

    def require_status(self) -> dict:
        if self.future is not None:
            raise ValueError("Wait for the current follower request")
        if (
            self.status is None or self.request_identity != self.identity()
            or self.request_revision != self.input_revision
            or time.monotonic() - self.status_at >= 10.0
        ):
            raise ValueError("Check follower first; position status must be < 10s old")
        return self.status

    def send_position(self) -> None:
        try:
            status = self.require_status()
            heading = self.context()
            correction = finite_number(self.correction.get(), "Correction")
            cue = estimated_position(
                self.latitude.get(), self.longitude.get(), self.distance.get(),
                self.altitude.get(), heading, correction,
            )
            distance = validate_cue_against_status(
                cue, status, self.assumed_position(),
            )
            bearing = (heading + correction) % 360
            position_note = (
                "Follower horizontal position is ASSUMED AT TOWER, not measured.\n"
                "Uncheck this assumption once the follower moves away."
                if self.follower_at_tower.get() else
                "Follower horizontal position uses its last reported GPS."
            )
            text = (
                f"ESTIMATED point, not measured target GPS:\n"
                f"{cue['lat']:.7f}, {cue['lon']:.7f}\n"
                f"Bearing used {bearing:.1f} deg "
                f"(compass {heading % 360:.1f} deg + correction {correction:+.1f} deg).\n"
                "The compass measures magnetic north; without a verified correction, "
                "the geographic bearing is approximate.\n"
                f"Horizontal distance "
                f"{float(self.distance.get()):.1f} m from tower.\n"
                f"Altitude {cue['alt_m']:.1f} m ABOVE FOLLOWER HOME, not terrain.\n"
                f"About {distance:.0f} m from follower's "
                f"{'assumed' if self.follower_at_tower.get() else 'last checked'} position.\n"
                f"{position_note}\n\n"
                "The follower may fly toward this point and attempt camera acquisition.\n"
                "Confirm clear airspace/terrain and correct altitude reference. Send?"
            )
            if not messagebox.askyesno("Send estimated target position", text, parent=self.frame):
                return
            self.require_status()
            current_heading = self.context()
            if abs((current_heading - heading + 180) % 360 - 180) > 2.0:
                raise ValueError("Antenna heading changed during confirmation; preview again")
            self.start_request(cue)
        except (ValueError, TypeError) as error:
            self.status_text.set(f"Not sent: {error}")

    def clear(self) -> None:
        if self.future is not None:
            self.status_text.set("Wait for the current follower request")
            return
        if messagebox.askyesno(
            "Clear position cue",
            "Clear the follower's position cue? This is NOT a flight stop or emergency stop.",
            parent=self.frame,
        ):
            self.start_request({"cmd": "clear"})

    def close(self) -> None:
        self.closed = True
        if self.after_id is not None:
            self.frame.after_cancel(self.after_id)
            self.after_id = None
        self.executor.shutdown(wait=False, cancel_futures=True)
