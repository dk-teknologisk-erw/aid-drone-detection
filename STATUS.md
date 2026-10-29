# Integration status

## Completed

- Replaced the old GPIO stepper, monolithic RF analyzer, duplicate script, backup communication package, and workspace template with one `aid_system` package containing specialized nodes.
- `mcu_bridge`: reconnecting Pico USB serial bridge; publishes angle/status and accepts mode, speed, target, and zero commands.
- `compass`: QMC5883P publisher using startup-relative heading; no absolute calibration required.
- `rf_sweep`: reconnecting RF Explorer publisher with 2.4 GHz, 5.8 GHz, and custom center/span selection.
- `detector`: selectable live-SNR/no-baseline detection (default) or persistent per-frequency-bin baselines. Repeated candidates are kept separately for 2.4 and 5.8 GHz, expire after 45 seconds, and are ranked across bands.
- `system_control`: calibrate/explore/focus/stop state handling. Dual calibration/explore scans one revolution per band, stops for at least five seconds while switching, then repeats. Focus Best switches to the winning candidate's band before pointing.
- `operator_gui`: host Tk GUI for modes, speed, Dual/2.4/5.8/custom RF selection, scan phase/status, candidate focus, manual orientation focus, and an on-demand embedded compressed camera Preview panel.
- `zmq_receiver`: standalone Tk test receiver for position/search JSON and binary compass Float32 messages, with simulated status/acknowledgements and no flight control.
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

On the host, install the GUI dependencies, build only `aid_system`, connect the bridge, and start the GUI:

```bash
sudo apt install python3-tk python3-pil python3-pil.imagetk
cd ros2_ws
./build_pc.sh
zenoh-bridge-ros2dds client -e tcp/<PI_IP>:7447
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch aid_system host_gui.launch.py
```

Install `python3-pil` and `python3-pil.imagetk` on the host for Preview. The Preview button expands the main window with a fixed 480 x 270 panel and subscribes to `/aid_camera/image_raw/compressed` only while visible.

Use the same `ROS_DOMAIN_ID` on both systems. Startup defaults are **5.8 GHz (5725-5850 MHz)** and **No baseline (live SNR)**. Press Explore without running Calibration, including after reboot while the test drone is already flying. This detects RF peaks, not drone identity.

No-baseline detection compares a 5 MHz power-smoothed peak with the current sweep's median noise floor, requiring 6 dB SNR. A signal occupying most of the scan can raise this floor and be missed; Wi-Fi can also pass the threshold. Two consecutive viable observations in the same band, within 5 MHz and 20 degrees and no more than two seconds apart, are required before Focus best is offered. Candidates expire after 45 seconds even if acquisition stops; a stronger historical peak is not refreshed merely because a weaker peak appears.

The GUI always shows the **raw maximum of the latest sweep**, including below-threshold signals and during Calibration/Focus/Tracking. This is independent of the smoothed candidate and focused/confirmed target, which are shown separately. Equal raw maxima select the first bin. Missing sweeps for three seconds or an RF disconnect mark the last maximum STALE and disable sending.

**Send Freq ROS2** publishes that displayed live maximum once as `std_msgs/msg/Float32`, in **MHz**, on `/aid/detection/freq`. It does not require confirmation. The status shows the published value and subscriber count, not a delivery acknowledgement. Confirming or tracking a target no longer automatically publishes frequency, so it cannot overwrite a manual send. Start the subscriber before clicking:

```bash
ros2 topic echo /aid/detection/freq std_msgs/msg/Float32
```

Focus best consumes a recent repeated candidate, switches RF band if necessary, and points to its recorded bearing. It holds there for an operator decision. **Confirm** becomes available only after motor arrival and a fresh viable signal within 10 MHz of the selected frequency. Confirm enters Tracking. **Discard candidate** clears detected, focused, and accepted targets and resumes Explore, even while moving toward a candidate. **Stop** stops motion instead. Changing detection method stops motion; changing RF configuration during Focus/Tracking also stops and clears that target.

Tracking starts in Hold. It searches within +/-10 MHz of the originally accepted frequency, rather than sampling a single fixed bin; the original anchor does not drift toward unrelated emitters. Retention uses 4 dB SNR in no-baseline mode, or 4 dB delta and z >= 3 in baseline mode. Oscillate samples at 3 degrees clockwise and counterclockwise, then shifts the center by 1 degree toward the stronger side when the mean RF amplitude difference exceeds 1 dB. Manual controls override automatic behavior. After three seconds without a viable matching measurement, automatic motion stops. Two consecutive matching measurements recover tracking automatically; Oscillate resumes if selected. Recovery only listens in the bounded frequency window at the held direction, not a full spatial/band search.

**Manual focus** now also offers **Move left / Move right** press-and-hold
buttons, independent of target detection and Tracking. Left decreases motor
angle (counterclockwise); right increases it (clockwise). Jogging cancels
automatic scanning/tracking, pending candidate focus and the selected/accepted
target, and leaves the system in manual Focus. Release, pointer leave, window
focus loss, minimization, close, or another control command stops the jog.
GUI keepalives are sent every 100 ms. The controller stops on a 0.5-second
keepalive/connected-motor-telemetry timeout, checked every 100 ms, plus
communication and physical deceleration delay. It advances short motor targets
using `manual_step_deg` (default 3 degrees) at the applied speed. A late keepalive
cannot restart a timed-out jog: release and press again. The existing MCU
watchdog remains the protection against MCU bridge communication loss.
Rebuild/restart both host GUI and Pi control package for this feature.

### Reusing a calibrated setup

Select **Saved baseline** to use the existing baseline without recalibrating. Calibration is still available and switches to baseline detection, replacing the previous calibration. Run it only with no test drone present, then leave Calibration to save. The file is `~/.ros/aid_baseline.json` under the detector process's home (or the `baseline_path` parameter); it is already persistent across reboot, so CSV export is not required for reuse. Keep separate copies for different physical setups if needed. Matching band/start/step/bin-count is required, and antenna placement/background should remain comparable.

Detector startup parameters include `detection_method` (`no_baseline` or `baseline`), `minimum_snr_db` (6), `tracking_minimum_snr_db` (4), `tracking_window_mhz` (10), and `candidate_lifetime_seconds` (45). Set `tracking_window_mhz` consistently on detector and system_control if overriding it. The existing 2402-2422 MHz exclusion remains; it does not affect the default 5.8 GHz scan or the GUI's raw maximum.

In Dual mode at 30 degrees/s, each revolution takes about 12 seconds. A repeating 2.4 + switch + 5.8 + switch cycle takes about 34 seconds. Measured full-sweep times are about 0.40 seconds at 2.4 GHz and 0.26 seconds at 5.8 GHz.

### Sending an estimated position to the follower

The host GUI includes a **Follower: estimated target position** panel. This uses
the supplied follower position protocol on **ZeroMQ REQ/REP port 5558**, not the
N/E/D search-point port 5559. A cue contains `lat`, `lon`, `alt_m`, and
`alt_ref: "rel"`. The follower may approach that estimated position and attempt
camera acquisition; receiver acceptance does not mean flight or acquisition is
complete. Detection confirmation never sends a flight cue automatically.

1. Enter the follower IP, position port (5558 by default), and optional shared
   secret. Secrets remain in memory and are not written to configuration/logs.
2. Enter **tower latitude/longitude**, **horizontal distance from the tower**,
   and **target altitude above the follower's takeoff/home** (not above the tower,
   current follower altitude, ground, or sea level).
3. Use the current antenna-aligned compass directly. **Heading correction** is
   optional and defaults to **0 degrees**; no true-heading measurement or
   verification checkbox is required. The calculation uses
   `bearing = compass heading + correction`. If a correction is known, it may
   include magnetic declination (east-positive) and mounting offset. With zero
   correction, magnetic heading is used as an approximation to geographic
   bearing; the preview warns that the point can be offset. Compass calibration
   still affects accuracy, and a constant correction cannot remove
   heading-dependent magnetic distortion.
4. Optionally select **Assume follower is CURRENTLY at tower (latitude/longitude)**.
   This explicitly reuses tower coordinates as the follower's **current horizontal
   position**, not its takeoff/home. It overrides the sender's use of reported
   follower GPS for distance checking, even if GPS is unavailable. Status and
   preview label it **ASSUMED AT TOWER**, not measured GPS. **Uncheck it as soon as
   the follower moves away**, then check follower again to use its reported GPS.
   Altitude remains above follower home; no altitude or home-coordinate equality
   is assumed. The position-cue payload and the follower's own telemetry are
   unchanged. The real receiver may still require GPS and reject the cue.
5. Point the antenna in the desired direction and stop it. **No RF detection,
   target confirmation, Tracking Hold, or particular detector/control mode is
   required.** Only fresh compass data (< 2 seconds) and connected, stationary
   motor telemetry (< 1 second) are required for the bearing. The compass is
   already antenna-aligned; motor angle is not added again.
6. Click **Check follower**. Local-position validity, reported altitude, and a
   successful status reply are still required; follower GPS is required unless
   the current-at-tower assumption is explicitly selected. The returned status
   must be less than ten seconds old when sending. For the test receiver, both
   simulated GPS fields may be left blank when this assumption is selected in
   the sender: it reports GPS unavailable rather than inventing coordinates.
7. Click **Preview / Send position**, verify the estimated GPS point, compass
   bearing/correction, distance, altitude reference, and clear flight path, then confirm.
   The held direction and status freshness are checked again after the popup.

Port **5558** describes an estimated leader/target GPS position; the supplied
sender describes the follower approaching it with a standoff, facing it, and
waiting for camera acquisition. Port **5559** instead describes a search point
as `n`, `e`, `d` metres from follower home (negative `d` means above home), which
the follower is described as flying to and searching around. These are different
payloads and intended behaviors, not interchangeable ports. This GUI implements
only the 5558 position payload; changing its port number does not convert it to
5559 local coordinates. Either form still needs a correctly referenced bearing
when generating a point from compass data.

Distance must be > 0 and <= 400 m from the tower; altitude must be 5-120 m above
follower home. The estimated point must also be within 400 m of the follower's
last checked GPS position (or explicitly assumed current tower position). These
mirror the example sender's limits, not
validated site/follower limits; the receiver must enforce its own limits,
geofence, terrain clearance, and flight-state policy.

**Clear position cue** is a separately confirmed request. It is not a flight
stop, return-to-home, or emergency stop. On timeout, the command outcome is
unknown: check the follower before retrying. Requests use a bounded three-second
send/reply budget and run outside the GUI thread. Changing IP/port/secret,
tower coordinates, or the current-at-tower checkbox invalidates cached status
and in-flight status replies. Optional HMAC signing follows the supplied compact,
sorted JSON format with `seq`, `t`, and `sig`; the real receiver's authentication,
clock/replay rules, home/local-origin semantics, and clear behavior still need
verification. There is no automatic command retry.

The point is an **operator estimate**, not measured target GPS: the RF detector
does not measure range/altitude or identify the emitter as a drone. Bearing
error creates lateral error (about 17 m at 100 m range for a 10-degree error).
Terrain/obstacles, GPS accuracy, magnetic interference, and target motion remain
operator/receiver concerns. A local simulated follower validates the wire
format and acknowledgements; physical follower integration has not been tested.

## ROS topics

- GUI command: `/aid/control` (`std_msgs/String`, JSON)
- MCU: `/aid/motor/{mode,speed_dps,target_deg,zero_deg,angle_deg,status}`
- RF: `/aid/rf/config`, `/aid/rf/sweep`, `/aid/rf/status`
- Compass: `/aid/compass/relative_heading_deg`, `/aid/compass/status`, `/aid/compass/zero`
- Detection: `/aid/detector/mode`, `/aid/detection`, `/aid/detector/status`
- Manually sent live maximum: `/aid/detection/freq` (`std_msgs/Float32`, MHz; GUI button only)
- Accepted target relative yaw: `/aid/detection/yaw_rel` (`std_msgs/Float32`, degrees clockwise from motor zero)
- Accepted target NED yaw: `/aid/detection/yaw_ned` (`std_msgs/Float32`, magnetic degrees clockwise from north)

## Validation and remaining checks

- Clean Jazzy build passes; combined bringup launch parsing and live camera/system startup pass. The focused `aid_system` test suite passes.
- A bounded full Pi launch starts and stops all five nodes cleanly while the RF node retries an absent device.
- Detector behavior test rejects baseline noise and detects a known 25 dB broadband outlier.
- Regression tests cover calibration-free 5.8 GHz peaks, noise rejection, frequency-window tracking, candidate repetition/expiry, confirmation after live RF verification, discard-to-explore, automatic signal recovery, and button-only Float32 delivery through ROS. Physical RF/motor validation remains necessary.
- ROS control test maps Explore to the MCU explore command.
- Compass published live ROS data. Pico firmware uses verified STEP GPIO 0, DIR GPIO 1, and a 50 microsecond STEP pulse; it boots stopped with JSON telemetry.
- Direct serial test commanded 91.5 degrees in 3 seconds at 30 degrees/s, then stopped. Explore and manual Focus also pass through ROS control.
- Smooth-motion test commanded exactly 180 degrees through ROS in 7.0 seconds: firmware ramped from about 2 to 30 degrees/s, decelerated into the target with zero reported step error, then disabled the A4988.
- RF dual-band standalone acquisition was previously verified. The RF Explorer was not visible during final ROS hardware testing, so reconnect/reconfiguration still needs a live end-to-end check.
- The GUI is syntax/build tested but not displayed here because this Pi currently lacks Tk/display access. Live compressed-frame receipt and JPEG decoding to the preview bounds pass.
- Motor angle is open-loop and resets to zero at MCU boot; physical zeroing or a home sensor is still needed for repeatable mechanical coordinates.
- RF outliers are candidates, not DJI identification; shared-band Wi-Fi remains a source of false positives.
- NED yaw currently uses the camera-aligned raw magnetic compass heading. Hard-iron/soft-iron calibration and magnetic declination correction are still required for accurate true-north yaw.