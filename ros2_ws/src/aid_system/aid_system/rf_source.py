import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def load_rfexplorer():
    configured = os.environ.get("AID_RFEXPLORER_PATH")
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        [
            Path(__file__).resolve().parents[4] / "RFExplorer-for-Python",
            Path("/opt/aid/RFExplorer-for-Python"),
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
            break
    import RFExplorer
    from RFExplorer import RFE_Common

    return RFExplorer, RFE_Common


RFExplorer, RFE_Common = load_rfexplorer()


@dataclass(frozen=True)
class ScanConfig:
    name: str
    start_mhz: float
    stop_mhz: float
    expansion: bool


def scan_config(selection: str, center_mhz: float = 0.0, span_mhz: float = 20.0) -> ScanConfig:
    if selection == "2.4":
        return ScanConfig("2.4ghz", 2400.0, 2483.5, True)
    if selection == "5.8":
        return ScanConfig("5.8ghz", 5725.0, 5850.0, False)
    if selection != "custom":
        raise ValueError(f"unknown RF selection: {selection}")
    if span_mhz <= 0:
        raise ValueError("custom RF span must be positive")
    start, stop = center_mhz - span_mhz / 2.0, center_mhz + span_mhz / 2.0
    if 15.0 <= start and stop <= 2700.0:
        return ScanConfig("custom", start, stop, True)
    if 4850.0 <= start and stop <= 6100.0:
        return ScanConfig("custom", start, stop, False)
    raise ValueError("custom range must fit 15-2700 MHz or 4850-6100 MHz")


class RfExplorerSource:
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
        self.wait_until(lambda: self.analyzer.ActiveModel != RFE_Common.eModel.MODEL_NONE, "configuration")
        if not self.analyzer.IsAnalyzer():
            raise RuntimeError("connected device is not an RF Explorer analyzer")
        self.analyzer.UseMaxHold = False

    def wait_until(self, predicate, description: str) -> None:
        deadline = time.monotonic() + self.timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for RF Explorer {description}")
            self.analyzer.ProcessReceivedString(True)
            time.sleep(0.01)

    def configure(self, config: ScanConfig) -> None:
        self.analyzer.SweepData.CleanAll()
        if self.analyzer.ExpansionBoardActive != config.expansion:
            self.analyzer.SendCommand("CM" + chr(int(config.expansion)))
            self.analyzer.SendCommand_RequestConfigData()
            self.wait_until(lambda: self.analyzer.ExpansionBoardActive == config.expansion, "module selection")
        self.analyzer.UpdateDeviceConfig(config.start_mhz, config.stop_mhz)
        self.wait_until(
            lambda: abs(self.analyzer.StartFrequencyMHZ - config.start_mhz) < 0.01
            and abs(self.analyzer.StopFrequencyMHZ - config.stop_mhz) < 0.1,
            "frequency configuration",
        )
        self.analyzer.SweepData.CleanAll()

    def next_sweep(self):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.analyzer.ProcessReceivedString(True)
            if self.analyzer.SweepData.Count:
                sweep = self.analyzer.SweepData.GetData(self.analyzer.SweepData.Count - 1)
                self.analyzer.SweepData.CleanAll()
                return sweep
            time.sleep(0.005)
        raise TimeoutError("timed out waiting for RF sweep")

    def close(self) -> None:
        self.analyzer.Close()