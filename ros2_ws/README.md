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

See `../STATUS.md` for MCU flashing, Zenoh commands, topics, and current status.
