#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RFEXPLORER_LIBRARY = REPOSITORY_ROOT / "RFExplorer-for-Python"
sys.path.insert(0, str(RFEXPLORER_LIBRARY))

import RFExplorer  # type: ignore[import-not-found]  # noqa: E402
from RFExplorer import RFE_Common  # type: ignore[import-not-found]  # noqa: E402


@dataclass(frozen=True)
class Band:
    name: str
    start_mhz: float
    stop_mhz: float
    expansion_module: bool


BANDS = {
    "2.4": Band("2.4ghz", 2400.0, 2483.5, True),
    "5.8": Band("5.8ghz", 5725.0, 5850.0, False),
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def default_port() -> str:
    matches = list(
        Path("/dev/serial/by-id").glob(
            "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_*-if00-port0"
        )
    )
    if len(matches) == 1:
        return str(matches[0].resolve())
    return "/dev/ttyUSB0"


class SweepSource:
    def __init__(self, port: str, baudrate: int, timeout: float) -> None:
        self.port = str(Path(port).resolve())
        self.baudrate = baudrate
        self.timeout = timeout
        self.analyzer = RFExplorer.RFECommunicator()
        self.analyzer.AutoConfigure = False

    def connect(self) -> None:
        self.analyzer.GetConnectedPorts()
        if not self.analyzer.ConnectPort(self.port, self.baudrate):
            raise RuntimeError(f"could not connect to RF Explorer on {self.port}")

        self.analyzer.SendCommand_RequestConfigData()
        self._wait_until(
            lambda: self.analyzer.ActiveModel != RFE_Common.eModel.MODEL_NONE
            and self.analyzer.StartFrequencyMHZ > 0,
            "RF Explorer configuration",
        )
        if not self.analyzer.IsAnalyzer():
            raise RuntimeError("connected RF Explorer is not a spectrum analyzer")

        self.analyzer.UseMaxHold = False

    def close(self) -> None:
        self.analyzer.Close()

    def _wait_until(self, predicate, description: str) -> None:
        deadline = time.monotonic() + self.timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {description}")
            self.analyzer.ProcessReceivedString(True)
            time.sleep(0.01)

    def configure(self, band: Band) -> None:
        self.analyzer.SweepData.CleanAll()
        if self.analyzer.ExpansionBoardActive != band.expansion_module:
            self.analyzer.SendCommand("CM" + chr(int(band.expansion_module)))
            self.analyzer.SendCommand_RequestConfigData()
            self._wait_until(
                lambda: self.analyzer.ExpansionBoardActive == band.expansion_module,
                f"{band.name} module selection",
            )

        self.analyzer.UpdateDeviceConfig(band.start_mhz, band.stop_mhz)
        self._wait_until(
            lambda: abs(self.analyzer.StartFrequencyMHZ - band.start_mhz) < 0.01
            and abs(self.analyzer.StopFrequencyMHZ - band.stop_mhz) < 0.1,
            f"{band.name} configuration",
        )

        if not (
            self.analyzer.MinFreqMHZ <= band.start_mhz
            and band.stop_mhz <= self.analyzer.MaxFreqMHZ
        ):
            raise RuntimeError(
                f"active {self.analyzer.ActiveModel.name} module covers "
                f"{self.analyzer.MinFreqMHZ:.1f}-{self.analyzer.MaxFreqMHZ:.1f} MHz, "
                f"not {band.start_mhz:.1f}-{band.stop_mhz:.1f} MHz"
            )
        self.analyzer.SweepData.CleanAll()

    def next_sweep(self):
        deadline = time.monotonic() + self.timeout
        while True:
            self.analyzer.ProcessReceivedString(True)
            if self.analyzer.SweepData.Count:
                sweep = self.analyzer.SweepData.GetData(
                    self.analyzer.SweepData.Count - 1
                )
                self.analyzer.SweepData.CleanAll()
                return sweep
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for RF sweep")
            time.sleep(0.005)

    def record(self, band: Band, sweep, sequence: int) -> dict[str, object]:
        amplitudes = [
            sweep.GetAmplitude_DBM(index)
            for index in range(sweep.TotalDataPoints)
        ]
        return {
            "schema": "aid.rf_sweep.v1",
            "sequence": sequence,
            "timestamp_utc": utc_timestamp(),
            "band": band.name,
            "module": self.analyzer.ActiveModel.name,
            "start_mhz": sweep.StartFrequencyMHZ,
            "stop_mhz": sweep.EndFrequencyMHZ,
            "step_mhz": sweep.StepFrequencyMHZ,
            "amplitudes_dbm": amplitudes,
        }


def selected_bands(selection: str) -> list[Band]:
    if selection == "both":
        return [BANDS["2.4"], BANDS["5.8"]]
    return [BANDS[selection]]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect full RF Explorer sweeps as replayable JSON Lines"
    )
    parser.add_argument("--band", choices=("2.4", "5.8", "both"), default="both")
    parser.add_argument("--port", default=default_port())
    parser.add_argument("--baudrate", type=int, default=500000)
    parser.add_argument("--sweeps-per-band", type=int, default=5)
    parser.add_argument(
        "--cycles", type=int, default=1, help="band cycles; 0 runs until Ctrl+C"
    )
    parser.add_argument("--settle-sweeps", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "data" / "rf_sweeps.jsonl"
    )
    arguments = parser.parse_args()
    if arguments.sweeps_per_band < 1:
        parser.error("--sweeps-per-band must be at least 1")
    if arguments.cycles < 0:
        parser.error("--cycles must be 0 or greater")
    if arguments.settle_sweeps < 0:
        parser.error("--settle-sweeps must be 0 or greater")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    source = SweepSource(arguments.port, arguments.baudrate, arguments.timeout)
    sequence = 0

    try:
        source.connect()
        with arguments.output.open("a", encoding="utf-8") as output:
            cycle = 0
            while arguments.cycles == 0 or cycle < arguments.cycles:
                for band in selected_bands(arguments.band):
                    source.configure(band)
                    print(
                        f"Scanning {band.name} with {source.analyzer.ActiveModel.name} "
                        f"({source.analyzer.MinFreqMHZ:.1f}-"
                        f"{source.analyzer.MaxFreqMHZ:.1f} MHz)"
                    )
                    for _ in range(arguments.settle_sweeps):
                        source.next_sweep()
                    for _ in range(arguments.sweeps_per_band):
                        sequence += 1
                        record = source.record(band, source.next_sweep(), sequence)
                        output.write(json.dumps(record, separators=(",", ":")) + "\n")
                        output.flush()
                        peak_index = max(
                            range(len(record["amplitudes_dbm"])),
                            key=record["amplitudes_dbm"].__getitem__,
                        )
                        peak_frequency = record["start_mhz"] + (
                            peak_index * record["step_mhz"]
                        )
                        print(
                            f"  sweep {sequence}: {peak_frequency:.3f} MHz, "
                            f"{record['amplitudes_dbm'][peak_index]:.1f} dBm"
                        )
                cycle += 1
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        source.close()

    print(f"Saved {sequence} sweeps to {arguments.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"RF collection failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error