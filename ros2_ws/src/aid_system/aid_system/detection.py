import json
import math
import statistics
from pathlib import Path


def smooth_dbm(amplitudes: list[float], radius: int) -> list[float]:
    powers = [10.0 ** (amplitude / 10.0) for amplitude in amplitudes]
    prefix = [0.0]
    for power in powers:
        prefix.append(prefix[-1] + power)
    result = []
    for index in range(len(amplitudes)):
        start = max(0, index - radius)
        stop = min(len(amplitudes), index + radius + 1)
        result.append(10.0 * math.log10((prefix[stop] - prefix[start]) / (stop - start)))
    return result


def configuration_key(sweep: dict) -> str:
    return f"{sweep['band']}:{float(sweep['start_mhz']):.6f}:{float(sweep['step_mhz']):.9f}:{len(sweep['amplitudes_dbm'])}"


def sweep_values(sweep: dict) -> list[float]:
    values = [float(value) for value in sweep["amplitudes_dbm"]]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("RF sweep must contain finite amplitudes")
    if not math.isfinite(float(sweep["start_mhz"])) or not math.isfinite(
        float(sweep["step_mhz"])
    ) or float(sweep["step_mhz"]) <= 0:
        raise ValueError("RF sweep must have a finite start and positive frequency step")
    return values


def live_maximum(sweep: dict) -> dict:
    values = sweep_values(sweep)
    index = max(range(len(values)), key=values.__getitem__)
    return {
        "frequency_mhz": float(sweep["start_mhz"]) + index * float(sweep["step_mhz"]),
        "amplitude_dbm": values[index],
    }


def analyze_without_baseline(
    sweep: dict,
    smoothing_mhz: float,
    minimum_snr_db: float,
    excluded_ranges_mhz: list[tuple[float, float]] | None = None,
    target_frequency_mhz: float | None = None,
    target_window_mhz: float = 10.0,
) -> dict:
    values = sweep_values(sweep)
    step = float(sweep["step_mhz"])
    start = float(sweep["start_mhz"])
    radius = max(0, round(smoothing_mhz / step / 2.0))
    observed = smooth_dbm(values, radius)
    allowed = [
        index for index in range(len(values))
        if not BaselineModel._excluded(sweep, index, radius, excluded_ranges_mhz)
    ]
    if not allowed:
        return {"viable": False, "reason": "all_bins_excluded"}
    noise = statistics.median([values[index] for index in allowed])
    if target_frequency_mhz is not None:
        allowed = [
            index for index in allowed
            if abs(start + index * step - target_frequency_mhz) <= target_window_mhz
        ]
    if not allowed:
        return {"viable": False, "reason": "target_outside_scan"}
    index = max(allowed, key=observed.__getitem__)
    snr = observed[index] - noise
    return {
        "viable": snr >= minimum_snr_db,
        "reason": "peak" if snr >= minimum_snr_db else "below_snr",
        "frequency_mhz": start + index * step,
        "amplitude_dbm": round(observed[index], 3),
        "noise_floor_dbm": round(noise, 3),
        "snr_db": round(snr, 3),
        "score": snr,
    }


class CandidateHistory:
    def __init__(self, lifetime_seconds: float = 45.0) -> None:
        self.lifetime_seconds = lifetime_seconds
        self.pending: dict[str, tuple[dict, float]] = {}
        self.best: dict[str, tuple[dict, float]] = {}

    def clear(self) -> None:
        self.pending.clear()
        self.best.clear()

    def observe(self, candidate: dict, now: float) -> None:
        band = str(candidate["band"])
        if not candidate.get("viable"):
            self.pending.pop(band, None)
            return
        previous = self.pending.get(band)
        self.pending[band] = (candidate.copy(), now)
        if previous is None:
            return
        prior, seen_at = previous
        angle_difference = abs(
            (candidate["antenna_angle_deg"] - prior["antenna_angle_deg"] + 180) % 360 - 180
        )
        if (
            now - seen_at > 2.0
            or abs(candidate["frequency_mhz"] - prior["frequency_mhz"]) > 5.0
            or angle_difference > 20.0
        ):
            return
        current = self.best.get(band)
        if (
            current is None
            or now - current[1] >= self.lifetime_seconds
            or candidate["score"] >= current[0]["score"]
        ):
            self.best[band] = (candidate.copy(), now)

    def candidates(self, now: float) -> dict[str, dict]:
        self.best = {
            band: entry for band, entry in self.best.items()
            if now - entry[1] < self.lifetime_seconds
        }
        return {
            band: {
                **candidate,
                "age_seconds": now - seen_at,
                "lifetime_seconds": self.lifetime_seconds,
            }
            for band, (candidate, seen_at) in self.best.items()
        }


class BaselineModel:
    def __init__(self) -> None:
        self.configurations: dict[str, dict] = {}

    def clear(self) -> None:
        self.configurations.clear()

    def update(self, sweep: dict) -> int:
        values = [float(value) for value in sweep["amplitudes_dbm"]]
        key = configuration_key(sweep)
        stats = self.configurations.setdefault(
            key,
            {
                "band": sweep["band"],
                "start_mhz": float(sweep["start_mhz"]),
                "step_mhz": float(sweep["step_mhz"]),
                "count": 0,
                "mean": [0.0] * len(values),
                "m2": [0.0] * len(values),
            },
        )
        stats["count"] += 1
        count = stats["count"]
        for index, value in enumerate(values):
            delta = value - stats["mean"][index]
            stats["mean"][index] += delta / count
            stats["m2"][index] += delta * (value - stats["mean"][index])
        return count

    def analyze(
        self,
        sweep: dict,
        smoothing_mhz: float,
        minimum_delta_db: float,
        minimum_z_score: float,
        std_floor_db: float,
        excluded_ranges_mhz: list[tuple[float, float]] | None = None,
        target_frequency_mhz: float | None = None,
        target_window_mhz: float | None = None,
    ) -> dict:
        key = configuration_key(sweep)
        stats = self.configurations.get(key)
        if stats is None or stats["count"] < 2:
            return {"viable": False, "reason": "no_baseline"}

        values = sweep_values(sweep)
        radius = max(0, round(smoothing_mhz / float(sweep["step_mhz"]) / 2.0))
        observed = smooth_dbm(values, radius)
        expected = smooth_dbm(stats["mean"], radius)
        deviations = [
            max(std_floor_db, math.sqrt(value / (stats["count"] - 1)))
            for value in stats["m2"]
        ]
        scores = [
            (observed[index] - expected[index]) / deviations[index]
            for index in range(len(observed))
        ]
        allowed = [
            index
            for index in range(len(scores))
            if not self._excluded(sweep, index, radius, excluded_ranges_mhz)
        ]
        if not allowed:
            return {"viable": False, "reason": "all_bins_excluded"}
        if target_frequency_mhz is not None and target_window_mhz is not None:
            allowed = [
                index for index in allowed
                if abs(
                    float(sweep["start_mhz"]) + index * float(sweep["step_mhz"])
                    - target_frequency_mhz
                ) <= target_window_mhz
            ]
            if not allowed:
                return {"viable": False, "reason": "target_outside_scan"}
        if target_frequency_mhz is None or target_window_mhz is not None:
            index = max(allowed, key=scores.__getitem__)
        else:
            index = min(
                allowed,
                key=lambda candidate: abs(
                    float(sweep["start_mhz"])
                    + candidate * float(sweep["step_mhz"])
                    - target_frequency_mhz
                ),
            )
        delta_db = observed[index] - expected[index]
        return {
            "viable": scores[index] >= minimum_z_score and delta_db >= minimum_delta_db,
            "reason": "outlier" if scores[index] >= minimum_z_score and delta_db >= minimum_delta_db else "within_baseline",
            "frequency_mhz": float(sweep["start_mhz"]) + index * float(sweep["step_mhz"]),
            "amplitude_dbm": round(observed[index], 3),
            "baseline_dbm": round(expected[index], 3),
            "delta_db": round(delta_db, 3),
            "z_score": round(scores[index], 3),
            "score": scores[index],
            "noise_floor_dbm": round(statistics.median(values), 3),
            "baseline_samples": stats["count"],
        }

    @staticmethod
    def _excluded(
        sweep: dict,
        index: int,
        radius: int,
        excluded_ranges_mhz: list[tuple[float, float]] | None,
    ) -> bool:
        if not excluded_ranges_mhz:
            return False
        step = float(sweep["step_mhz"])
        frequency = float(sweep["start_mhz"]) + index * step
        # Widen by the smoothing radius so excluded energy cannot leak into neighbours.
        margin = radius * step
        return any(
            low - margin <= frequency <= high + margin
            for low, high in excluded_ranges_mhz
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "aid.rf_baseline.v1", "configurations": self.configurations}))

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        document = json.loads(path.read_text())
        if document.get("schema") != "aid.rf_baseline.v1":
            raise ValueError("unsupported baseline schema")
        self.configurations = document["configurations"]
        return True