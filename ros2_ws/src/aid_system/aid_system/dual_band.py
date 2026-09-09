class DualBandScheduler:
    BANDS = ("2.4", "5.8")

    def __init__(self, switch_seconds: float = 5.0) -> None:
        self.switch_seconds = switch_seconds
        self.active_band = "2.4"
        self.phase = "idle"
        self.rotation_degrees = 0.0
        self.last_angle: float | None = None
        self.switch_ready_at = 0.0

    def start_scan(self, band: str, angle: float) -> None:
        if band not in self.BANDS:
            raise ValueError(f"invalid dual band: {band}")
        self.active_band = band
        self.phase = "scanning"
        self.rotation_degrees = 0.0
        self.last_angle = angle % 360.0

    def start_switch(self, band: str, now: float, angle: float) -> None:
        if band not in self.BANDS:
            raise ValueError(f"invalid dual band: {band}")
        self.active_band = band
        self.phase = "switching"
        self.rotation_degrees = 0.0
        self.last_angle = angle % 360.0
        self.switch_ready_at = now + self.switch_seconds

    def observe_angle(self, angle: float, now: float) -> str | None:
        angle %= 360.0
        if self.phase != "scanning":
            self.last_angle = angle
            return None
        if self.last_angle is None:
            self.last_angle = angle
            return None

        delta = (angle - self.last_angle) % 360.0
        self.last_angle = angle
        if delta > 180.0:
            return None
        self.rotation_degrees += delta
        if self.rotation_degrees < 359.0:
            return None

        self.start_switch("5.8" if self.active_band == "2.4" else "2.4", now, angle)
        return self.active_band

    def ready_to_resume(self, now: float, ready_band: str | None, angle: float) -> bool:
        if (
            self.phase != "switching"
            or now < self.switch_ready_at
            or ready_band != self.active_band
        ):
            return False
        self.start_scan(self.active_band, angle)
        return True

    def cancel(self) -> None:
        self.phase = "idle"
        self.rotation_degrees = 0.0
        self.last_angle = None

    @property
    def switch_remaining(self) -> float:
        return self.switch_ready_at