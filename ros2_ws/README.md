# AID ROS 2 workspace

```bash
source /opt/ros/jazzy/setup.bash
./setup.sh
./build.sh
source install/setup.bash
ros2 launch aid_bringup aid_bringup.launch.py
```

The combined launch starts `aid_camera_server` and all `aid_system` Pi nodes with
ROS domain 0 and localhost-only DDS discovery. The system service installation
and management commands are documented in `../SETUP.md`.

Build and run only the GUI package on the host PC (no Pylon/camera SDK needed):

```bash
sudo apt install python3-tk python3-pil python3-pil.imagetk
./build_pc.sh
source install/setup.bash
ros2 launch aid_system host_gui.launch.py
```

Click **Preview** to show or hide the compressed camera stream inside the main GUI window.

The RF system starts on **5.8 GHz** with **No baseline (live SNR)** detection.
Press **Explore** without calibration. **Saved baseline** remains available for
reusing a calibrated setup after reboot.

The live raw maximum is always displayed separately from the selected target.
**Send Freq ROS2** publishes it once as Float32 MHz on `/aid/detection/freq`.
**Focus best** points to a recent repeated candidate; **Confirm** requires arrival
and a live signal, and **Discard candidate** resumes Explore. **Stop** stops motion.
See `../STATUS.md` for tracking recovery, freshness checks, and detection limits.

In **Manual focus**, hold **Move left** (counterclockwise) or **Move right**
(clockwise) to turn without a detected target. Release or move the pointer off
the button to stop. Changing operation, losing window focus, or losing the
100 ms keepalive also stops this jog (controller timeout: 0.5 seconds, plus
timer/transport/deceleration delay). Manual focus leaves automatic tracking
and clears the selected/accepted target. Rebuild both the host GUI and Pi
`aid_system` package for the new jog command.

The **Follower: estimated target position** panel can send a manually confirmed
position cue over ZeroMQ REQ/REP (default port 5558). Enter tower GPS, horizontal
target distance, and altitude above follower home. The current antenna-aligned
compass supplies the bearing; heading correction is optional and defaults to 0.
Uncorrected magnetic heading produces an approximate GPS point. The optional
**Assume follower is CURRENTLY at tower** checkbox reuses tower latitude/longitude
as its current horizontal position for sender-side checks. Uncheck once the
follower moves away; this is not a home-location setting or measured GPS.
Altitude still means above follower home.
No RF detection, target confirmation, or Tracking Hold is required. Sending
requires fresh compass/stationary-motor telemetry, a recent **Check follower**
response, and explicit preview confirmation. This sends a GPS-position payload
on 5558, not the local N/E/D search-point payload on 5559. See `../STATUS.md` for
coordinate references, flight-behavior differences, limits, and receiver checks.

## ZeroMQ test receiver GUI

After building and sourcing this workspace:

```bash
ros2 run aid_system zmq_receiver
```

Or, from the repository root, without a ROS installation (Python Tkinter and
pyzmq are required):

```bash
PYTHONPATH=ros2_ws/src/aid_system python3 -m aid_system.zmq_receiver
```

This is a **test receiver only**, not a drone controller. Set the bind IP to
`127.0.0.1` for the same computer, or `0.0.0.0` to receive from other computers
on a trusted network. Point the sending GUI at the receiver computer's real
IP, never `0.0.0.0`. Default ports are:

- **5558**: ZeroMQ REP, target-position JSON.
- **5559**: ZeroMQ REP, local search-point JSON.
- **5555**: ZeroMQ PULL, little-endian binary Float32 compass heading.

For a quick test, leave both simulated GPS fields blank and tick **Assume follower
is CURRENTLY at tower** in the sender. **Check follower** then uses the tower
coordinates already entered in the sender; no duplicate GPS entry is needed.
The test receiver honestly reports `gps_ok: false`, not a fabricated GPS fix.
Without that checkbox, enter simulated follower latitude/longitude in the
receiver. Altitude and North/East are simulated values relative to home (defaults
0); they must still be valid numbers. Partly filled or malformed GPS is rejected.
Press **Start listening**. The log shows actual arrivals and simulated replies.
**Send orientation** uses the configured compass port; the follower panel uses
5558. The ROS2 frequency button is not ZeroMQ and will not appear here.

The receiver does not verify signatures, enforce real flight limits, or perform
flight actions. Its ACKs are explicitly marked test-only. It can accept signed
messages for transport testing but does not prove real follower authentication.
Do not bind the same ports on the same address as a running follower receiver.

See `../STATUS.md` for MCU flashing, Zenoh commands, topics, and current status.
