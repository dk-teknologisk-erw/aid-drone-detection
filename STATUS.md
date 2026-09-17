# Integration status

## Completed

- Replaced the old GPIO stepper, monolithic RF analyzer, duplicate script, backup communication package, and workspace template with one `aid_system` package containing specialized nodes.
- `mcu_bridge`: reconnecting Pico USB serial bridge; publishes angle/status and accepts mode, speed, target, and zero commands.
- `compass`: QMC5883P publisher using startup-relative heading; no absolute calibration required.
- `rf_sweep`: reconnecting RF Explorer publisher with 2.4 GHz, 5.8 GHz, and custom center/span selection.
- `detector`: persistent per-frequency-bin baselines and best candidates kept separately for 2.4 and 5.8 GHz, with one normalized best candidate across both bands.
- `system_control`: calibrate/explore/focus/stop state handling. Dual calibration/explore scans one revolution per band, stops for at least five seconds while switching, then repeats. Focus Best switches to the winning candidate's band before pointing.
- `operator_gui`: host Tk GUI for modes, speed, Dual/2.4/5.8/custom RF selection, scan phase/status, candidate focus, and manual orientation focus without a candidate.
- `aid_motor_controller.ino`: Pico firmware with 30 degrees/s2 acceleration/deceleration, adjustable speed, target motion, stop/zero, 10 Hz JSON telemetry, and active-low A4988 enable on GPIO 2. Coil power is disabled while stopped; a 1.5-second command watchdog stops motion after bridge/USB loss.
- `aid_bringup`: combines the camera launch and all Pi-side AID nodes under one launch. `aid-ros2-bringup.service` provides root-managed start/stop/enable with graceful ROS shutdown.

## Run

Flash the Pico (motor starts stopped):

```bash
./mcu/flash_pico.sh aid_motor_controller
```

Build and run on the Pi:

```bash
cd ros2_ws
source /opt/ros/jazzy/setup.bash
./setup.sh
./build.sh
source install/setup.bash
ros2 launch aid_bringup aid_bringup.launch.py
```

Install and manage the combined system service:

```bash
sudo ./scripts/install_aid_bringup_service.sh
sudo systemctl start aid-ros2-bringup
sudo systemctl stop aid-ros2-bringup
sudo systemctl enable aid-ros2-bringup
```

Bridge Pi ROS to Zenoh:

```bash
zenoh-bridge-ros2dds router -l tcp/0.0.0.0:7447
```

On the host, build the same workspace, install `python3-tk`, connect the bridge, and start the GUI:

```bash
zenoh-bridge-ros2dds client -e tcp/<PI_IP>:7447
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
ros2 launch aid_system host_gui.launch.py
```

Use the same `ROS_DOMAIN_ID` on both systems. Select an RF range, run Calibration with no drone present, then switch to Explore. Focus best becomes available only for a viable outlier.

In Dual mode at 30 degrees/s, each revolution takes about 12 seconds. A repeating 2.4 + switch + 5.8 + switch cycle takes about 34 seconds. Measured full-sweep times are about 0.40 seconds at 2.4 GHz and 0.26 seconds at 5.8 GHz.

## ROS topics

- GUI command: `/aid/control` (`std_msgs/String`, JSON)
- MCU: `/aid/motor/{mode,speed_dps,target_deg,zero_deg,angle_deg,status}`
- RF: `/aid/rf/config`, `/aid/rf/sweep`, `/aid/rf/status`
- Compass: `/aid/compass/relative_heading_deg`, `/aid/compass/status`, `/aid/compass/zero`
- Detection: `/aid/detector/mode`, `/aid/detection`, `/aid/detector/status`

## Validation and remaining checks

- Clean Jazzy build passes; combined bringup launch parsing and live camera/system startup pass. All 7 focused `aid_system` tests pass.
- A bounded full Pi launch starts and stops all five nodes cleanly while the RF node retries an absent device.
- Detector behavior test rejects baseline noise and detects a known 25 dB broadband outlier.
- ROS control test maps Explore to the MCU explore command.
- Compass published live ROS data. Pico firmware uses verified STEP GPIO 0, DIR GPIO 1, and a 50 microsecond STEP pulse; it boots stopped with JSON telemetry.
- Direct serial test commanded 91.5 degrees in 3 seconds at 30 degrees/s, then stopped. Explore and manual Focus also pass through ROS control.
- Smooth-motion test commanded exactly 180 degrees through ROS in 7.0 seconds: firmware ramped from about 2 to 30 degrees/s, decelerated into the target with zero reported step error, then disabled the A4988.
- RF dual-band standalone acquisition was previously verified. The RF Explorer was not visible during final ROS hardware testing, so reconnect/reconfiguration still needs a live end-to-end check.
- The GUI is syntax/build tested but not displayed here because this Pi currently lacks Tk/display access; install `python3-tk` on the host.
- Motor angle is open-loop and resets to zero at MCU boot; physical zeroing or a home sensor is still needed for repeatable mechanical coordinates.
- RF outliers are candidates, not DJI identification; shared-band Wi-Fi remains a source of false positives.