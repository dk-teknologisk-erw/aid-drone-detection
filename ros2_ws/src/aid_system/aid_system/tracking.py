from __future__ import annotations

import statistics


def normalize_angle(angle: float) -> float:
    return angle % 360.0


def angular_difference(target: float, current: float) -> float:
    return (target - current + 180.0) % 360.0 - 180.0


class TrackingSearch:
    def __init__(
        self,
        span_deg: float,
        correction_deg: float,
        deadband_db: float,
        sample_seconds: float,
    ) -> None:
        self.span_deg = span_deg
        self.correction_deg = correction_deg
        self.deadband_db = deadband_db
        self.sample_seconds = sample_seconds
        self.center_deg = 0.0
        self.target_deg: float | None = None
        self.phase = "idle"
        self.sample_until = 0.0
        self.samples: list[float] = []
        self.clockwise_score: float | None = None

    def reset(self, center_deg: float) -> None:
        self.center_deg = normalize_angle(center_deg)
        self.target_deg = None
        self.phase = "idle"
        self.sample_until = 0.0
        self.samples.clear()
        self.clockwise_score = None

    def start(self, center_deg: float) -> float:
        self.reset(center_deg)
        return self._move_to("clockwise")

    def target_reached(self, now: float) -> None:
        if self.phase not in {"move_clockwise", "move_counterclockwise"}:
            return
        self.phase = self.phase.replace("move_", "sample_")
        self.sample_until = now + self.sample_seconds
        self.samples.clear()

    def add_sample(self, delta_db: float) -> None:
        if self.phase.startswith("sample_"):
            self.samples.append(delta_db)

    def advance(self, now: float) -> float | None:
        if not self.phase.startswith("sample_") or now < self.sample_until:
            return None
        score = statistics.fmean(self.samples) if self.samples else None
        if self.phase == "sample_clockwise":
            self.clockwise_score = score
            return self._move_to("counterclockwise")

        counterclockwise_score = score
        if self.clockwise_score is not None and counterclockwise_score is not None:
            difference = self.clockwise_score - counterclockwise_score
            if difference > self.deadband_db:
                self.center_deg = normalize_angle(
                    self.center_deg + self.correction_deg
                )
            elif difference < -self.deadband_db:
                self.center_deg = normalize_angle(
                    self.center_deg - self.correction_deg
                )
        self.clockwise_score = None
        return self._move_to("clockwise")

    def _move_to(self, direction: str) -> float:
        offset = self.span_deg if direction == "clockwise" else -self.span_deg
        self.target_deg = normalize_angle(self.center_deg + offset)
        self.phase = f"move_{direction}"
        self.samples.clear()
        return self.target_deg