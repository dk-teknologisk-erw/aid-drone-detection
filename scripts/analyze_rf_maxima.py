#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path


def smooth_dbm(amplitudes: list[float], radius: int) -> list[float]:
    powers_mw = [10.0 ** (amplitude / 10.0) for amplitude in amplitudes]
    prefix = [0.0]
    for power in powers_mw:
        prefix.append(prefix[-1] + power)

    smoothed = []
    for index in range(len(amplitudes)):
        start = max(0, index - radius)
        stop = min(len(amplitudes), index + radius + 1)
        mean_power = (prefix[stop] - prefix[start]) / (stop - start)
        smoothed.append(10.0 * math.log10(mean_power))
    return smoothed


def local_maxima(values: list[float]) -> list[int]:
    maxima = []
    for index, value in enumerate(values):
        left = values[index - 1] if index else -math.inf
        right = values[index + 1] if index + 1 < len(values) else -math.inf
        if value >= left and value >= right and (value > left or value > right):
            maxima.append(index)
    return maxima


def analyze_sweep(
    record: dict[str, object],
    smoothing_mhz: float,
    minimum_snr_db: float,
    minimum_separation_mhz: float,
    maximum_peaks: int,
) -> list[dict[str, object]]:
    if record.get("schema") != "aid.rf_sweep.v1":
        raise ValueError("unsupported or missing sweep schema")

    amplitudes_value = record.get("amplitudes_dbm")
    if not isinstance(amplitudes_value, list) or not amplitudes_value:
        raise ValueError("amplitudes_dbm must be a non-empty list")
    amplitudes = [float(value) for value in amplitudes_value]
    if not all(math.isfinite(value) for value in amplitudes):
        raise ValueError("amplitudes_dbm contains a non-finite value")

    start_mhz = float(record["start_mhz"])
    step_mhz = float(record["step_mhz"])
    if step_mhz <= 0:
        raise ValueError("step_mhz must be positive")

    smoothing_radius = max(0, round(smoothing_mhz / step_mhz / 2.0))
    separation_bins = max(1, round(minimum_separation_mhz / step_mhz))
    smoothed = smooth_dbm(amplitudes, smoothing_radius)
    noise_floor_dbm = statistics.median(amplitudes)
    candidates = sorted(local_maxima(smoothed), key=smoothed.__getitem__, reverse=True)
    selected: list[int] = []
    for index in candidates:
        if smoothed[index] - noise_floor_dbm < minimum_snr_db:
            continue
        if any(abs(index - other) < separation_bins for other in selected):
            continue
        selected.append(index)
        if len(selected) == maximum_peaks:
            break

    peaks = []
    for rank, index in enumerate(selected, start=1):
        window_start = max(0, index - smoothing_radius)
        window_stop = min(len(amplitudes), index + smoothing_radius + 1)
        strongest_index = max(
            range(window_start, window_stop), key=amplitudes.__getitem__
        )
        peaks.append(
            {
                "schema": "aid.rf_peak.v1",
                "source_sequence": record.get("sequence"),
                "timestamp_utc": record.get("timestamp_utc"),
                "band": record.get("band"),
                "rank": rank,
                "frequency_mhz": start_mhz + index * step_mhz,
                "channel_amplitude_dbm": round(smoothed[index], 3),
                "strongest_bin_frequency_mhz": start_mhz
                + strongest_index * step_mhz,
                "strongest_bin_amplitude_dbm": amplitudes[strongest_index],
                "noise_floor_dbm": round(noise_floor_dbm, 3),
                "snr_db": round(smoothed[index] - noise_floor_dbm, 3),
                "smoothing_width_mhz": smoothing_radius * 2.0 * step_mhz,
            }
        )
    return peaks


def default_output(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_peaks.jsonl")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect significant RF maxima in recorded full sweeps"
    )
    parser.add_argument("input", type=Path, help="JSONL created by collect_rf_sweeps.py")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoothing-mhz", type=float, default=5.0)
    parser.add_argument("--min-snr-db", type=float, default=6.0)
    parser.add_argument("--min-separation-mhz", type=float, default=10.0)
    parser.add_argument("--max-peaks", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.smoothing_mhz < 0:
        parser.error("--smoothing-mhz must be non-negative")
    if arguments.min_separation_mhz <= 0:
        parser.error("--min-separation-mhz must be positive")
    if arguments.max_peaks < 1:
        parser.error("--max-peaks must be at least 1")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    output_path = arguments.output or default_output(arguments.input)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sweep_count = 0
    peak_count = 0

    with arguments.input.open(encoding="utf-8") as input_file, output_path.open(
        "w", encoding="utf-8"
    ) as output_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                peaks = analyze_sweep(
                    record,
                    arguments.smoothing_mhz,
                    arguments.min_snr_db,
                    arguments.min_separation_mhz,
                    arguments.max_peaks,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"line {line_number}: {error}") from error

            sweep_count += 1
            for peak in peaks:
                output_file.write(json.dumps(peak, separators=(",", ":")) + "\n")
            peak_count += len(peaks)

    print(f"Analyzed {sweep_count} sweeps; wrote {peak_count} peaks to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"RF analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error