# AID ROS 2 workspace

```bash
source /opt/ros/jazzy/setup.bash
./setup.sh
./build.sh
source install/setup.bash
ros2 launch aid_system pi_system.launch.py
```

Run the host GUI after building the same workspace on the host:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch aid_system host_gui.launch.py
```

See `../STATUS.md` for MCU flashing, Zenoh commands, topics, and current status.
