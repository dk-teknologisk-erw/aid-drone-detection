#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROS_DISTRO=${ROS_DISTRO:-jazzy}

source "/opt/ros/$ROS_DISTRO/setup.bash"
set -u

if ! /usr/bin/python3 -c 'import tkinter; from PIL import Image, ImageTk' 2>/dev/null; then
    printf '%s\n' 'Missing GUI dependencies. Install them with:' >&2
    printf '%s\n' '  sudo apt install python3-tk python3-pil python3-pil.imagetk' >&2
    exit 1
fi

cd "$SCRIPT_DIR"
colcon build \
    --merge-install \
    --symlink-install \
    --packages-select aid_system

printf '%s\n' 'PC build complete. Run:'
printf '%s\n' '  source install/setup.bash'
printf '%s\n' '  ros2 launch aid_system host_gui.launch.py'