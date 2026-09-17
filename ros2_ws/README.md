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

Click **Preview** to open the on-demand compressed camera stream.

See `../STATUS.md` for MCU flashing, Zenoh commands, topics, and current status.
