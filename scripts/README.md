# Standalone sensor scripts

These scripts collect independent, timestamped records. They do not depend on
ROS 2 and do not couple RF measurements to compass headings.

Check the Pico, RF Explorer, compass, Zenoh listener, and last USB disconnects:

```bash
./scripts/check_connections.py
```

## RF sweep collection

Collect five full sweeps from each DJI-relevant ISM band:

```bash
./scripts/collect_rf_sweeps.py
```

Collect continuously until Ctrl+C:

```bash
./scripts/collect_rf_sweeps.py --band both --cycles 0 --output data/rf_sweeps.jsonl
```

The collector selects the WSUB3G expansion for 2400-2483.5 MHz and the 6G main
module for 5725-5850 MHz. Each `aid.rf_sweep.v1` JSONL record contains all 112
frequency bins so recordings can be replayed with different analysis settings.

## RF maxima analysis

```bash
./scripts/analyze_rf_maxima.py data/rf_sweeps.jsonl
```

The analyzer smooths amplitudes in linear power, finds separated local maxima,
and rejects candidates below the median noise floor plus 6 dB by default. Tune
the detector without recollecting data, for example:

```bash
./scripts/analyze_rf_maxima.py data/rf_sweeps.jsonl \
    --smoothing-mhz 10 --min-snr-db 8 --min-separation-mhz 20
```

The output uses the `aid.rf_peak.v1` schema. A peak is an RF-energy candidate,
not proof of a drone: Wi-Fi and other transmitters share both bands. DJI
classification will require additional temporal or spectral features.

## Compass

Human-readable test:

```bash
./scripts/test_compass.py
```

Continuous timestamped JSONL for later composition:

```bash
./scripts/test_compass.py --samples 0 --json
```

Compass headings are uncalibrated. Hard-iron/soft-iron calibration and mounting
offset correction are required before using them as absolute directions.