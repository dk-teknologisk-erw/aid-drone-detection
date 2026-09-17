#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
    printf 'Run this installer with sudo.\n' >&2
    exit 1
fi

PROJECT_DIR=/home/dti-242/aid-drone-detection
chmod 0755 "$PROJECT_DIR/scripts/run_aid_bringup.sh"
install -m 0644 "$PROJECT_DIR/systemd/aid-ros2-bringup.service" /etc/systemd/system/aid-ros2-bringup.service
systemctl daemon-reload

printf '%s\n' 'Installed aid-ros2-bringup.service (not enabled and not started).'
printf '%s\n' 'Start:  sudo systemctl start aid-ros2-bringup'
printf '%s\n' 'Enable: sudo systemctl enable aid-ros2-bringup'
printf '%s\n' 'Stop:   sudo systemctl stop aid-ros2-bringup'