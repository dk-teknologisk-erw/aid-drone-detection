The code is to be deployed on a Rapsberry Pi 4.

It is powered through its 5V and GND Pin via an external DCDC converter with 24V/5V.
The RPi is connected via GPIO pin 2 (SDA) and 3 (SCL) to a magnetometer.

Via its USB interface it is connected to the the RF Explorer that is ttyUSB0 and a RPi pico 2 (called mcu, ttyACM0)  that controls a stepmotor. The script can be flashed to the mcu via mcu/flash_pico.sh <name>.

The RPI is connected via a slip-contact to power and data to the mcu. 

The antenna of the RFExplorer is placed on a spinning tower (spinning controller via mcu). It is one directional and should thereby be able to identify the direction of a signal by amplitude comparison.
I want to have three modes, one is exploring where the tower turns and the RF identifies the direction of the strongest signal, a focus mode, where the motor actively points in this dirdection and a calibration mode, where a baseline for a detection is laid, here i kknow that no drone signals are present, i want to generate a dataset that is the non-drone reference to base my outlier detection upon. (ran during explore mode). In operation i want to be able to activate focussing on the best candidate (if outlier found), for now I want this to happen only with manual trigger whenever there is a statistically viable outlier candiate.

## Verified current state

- Compass: QMC5883P on `/dev/i2c-1`, address `0x2c`; heading needs hard/soft-iron and mounting-offset calibration.
- RF Explorer: 6G main module plus WSUB3G expansion. Explicitly select WSUB3G for 2400-2483.5 MHz and 6G for 5725-5850 MHz.
- Dual mode alternates one full rotation at 2.4 GHz and one at 5.8 GHz, with the motor stopped for at least 5 seconds during each module switch. Calibration stores independent baselines; Focus Best selects across both bands.
- `scripts/collect_rf_sweeps.py` records timestamped full 112-bin sweeps (`aid.rf_sweep.v1` JSONL); `scripts/analyze_rf_maxima.py` produces smoothed, SNR-filtered candidates (`aid.rf_peak.v1`).
- `scripts/test_compass.py --samples 0 --json` emits independent timestamped compass records. No sensor/ROS2 coupling exists yet.
- RF maxima alone cannot identify DJI: Wi-Fi and other emitters share both bands. DJI classification needs later temporal/spectral features and calibration data.
- Use real-time RF amplitudes, not persistent max-hold, for direction comparison. Match candidates across headings by band/frequency and timestamp.
- Verified motor wiring uses STEP GPIO 0, DIR GPIO 1, and active-low A4988 ENABLE GPIO 2. Integrated firmware disables coil power while stopped.
- USB may reconnect through the slip contact; prefer `/dev/serial/by-id` identity over fixed tty numbers. Setup commands are in `SETUP.md`.