#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
    printf 'Run this installer with sudo.\n' >&2
    exit 1
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -m 0644 "$SCRIPT_DIR/98-picotool.rules" /etc/udev/rules.d/98-picotool.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=2e8a

printf 'Pico udev rules installed. Unplug and reconnect the Pico before flashing.\n'