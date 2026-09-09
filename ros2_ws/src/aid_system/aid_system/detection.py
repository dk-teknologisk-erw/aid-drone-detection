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
    ) -> dict:
        key = configuration_key(sweep)
        stats = self.configurations.get(key)
        if stats is None or stats["count"] < 2:
            return {"viable": False, "reason": "no_baseline"}

        values = [float(value) for value in sweep["amplitudes_dbm"]]
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
        index = max(range(len(scores)), key=scores.__getitem__)
        delta_db = observed[index] - expected[index]
        return {
            "viable": scores[index] >= minimum_z_score and delta_db >= minimum_delta_db,
            "reason": "outlier" if scores[index] >= minimum_z_score and delta_db >= minimum_delta_db else "within_baseline",
            "frequency_mhz": float(sweep["start_mhz"]) + index * float(sweep["step_mhz"]),
            "amplitude_dbm": round(observed[index], 3),
            "baseline_dbm": round(expected[index], 3),
            "delta_db": round(delta_db, 3),
            "z_score": round(scores[index], 3),
            "noise_floor_dbm": round(statistics.median(values), 3),
            "baseline_samples": stats["count"],
        }

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