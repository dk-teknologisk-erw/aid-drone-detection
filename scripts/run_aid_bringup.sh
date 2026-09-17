#!/usr/bin/env bash

set -eo pipefail

PROJECT_DIR=/home/dti-242/aid-drone-detection
source /opt/ros/jazzy/setup.bash
source "$PROJECT_DIR/ros2_ws/install/setup.bash"

export HOME=/home/dti-242
export ROS_DOMAIN_ID=0
export ROS_LOCALHOST_ONLY=1
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export PYTHONUNBUFFERED=1

exec ros2 launch aid_bringup aid_bringup.launch.py