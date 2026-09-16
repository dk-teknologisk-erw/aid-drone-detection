# AID Camera Server

Temporary ROS 2 Jazzy package for one Basler camera. The node opens the camera
directly through pylon and publishes:

- `/aid_camera/image_raw` (`sensor_msgs/msg/Image`, RGB8)
- `/aid_camera/camera_info` (`sensor_msgs/msg/CameraInfo`)

It reconnects after camera loss. If more than one camera is attached,
`serial_number` must select one.

## Requirements

- Ubuntu 24.04 ARM64
- ROS 2 Jazzy with `camera_info_manager`, `rclcpp`, and `sensor_msgs`
- Basler pylon ARM64 SDK installed at `/opt/pylon`

## Build

Source ROS 2, then build this isolated package from any directory:

```bash
source /opt/ros/jazzy/setup.bash
colcon --log-base /tmp/aid_camera_server/log build \
  --base-paths /absolute/path/to/aid_camera_server \
  --build-base /tmp/aid_camera_server/build \
  --install-base /tmp/aid_camera_server/install
```

## Run

```bash
source /opt/ros/jazzy/setup.bash
source /tmp/aid_camera_server/install/setup.bash
ros2 launch aid_camera_server aid_camera_server.launch.yaml
```

With multiple attached cameras, set the serial in
`config/aid_camera_server.yaml` before building or pass it at runtime:

```bash
ros2 run aid_camera_server aid_camera_server_node --ros-args \
  -r __ns:=/aid_camera \
  -p serial_number:=12345678
```

Verify publishing:

```bash
ros2 topic hz /aid_camera/image_raw
ros2 topic echo /aid_camera/camera_info --once
```

View locally:

```bash
ros2 run rqt_image_view rqt_image_view /aid_camera/image_raw
```

## Calibration

Set `camera_info_url` to a ROS camera calibration YAML, for example:

```yaml
camera_info_url: file:///home/user/camera_calibration.yaml
```

Without calibration, `camera_info` still reports image dimensions but its
calibration matrices remain zero.