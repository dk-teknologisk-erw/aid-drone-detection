#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="/home/dti-242/aid-drone-detection/pylon-26.06.2_linux-aarch64_debs.tar.gz"

[[ "$(dpkg --print-architecture)" == "arm64" ]] || {
  echo "ERROR: ARM64 Ubuntu required." >&2
  exit 1
}

[[ -f "$ARCHIVE" ]] || {
  echo "ERROR: Archive not found: $ARCHIVE" >&2
  exit 1
}

sudo -v

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Archive:"
sha256sum "$ARCHIVE"

echo "Extracting..."
tar -xzf "$ARCHIVE" -C "$WORK_DIR"

PYLON_DEB=""

while IFS= read -r -d '' deb; do
  package="$(dpkg-deb -f "$deb" Package)"
  architecture="$(dpkg-deb -f "$deb" Architecture)"
  version="$(dpkg-deb -f "$deb" Version)"

  if [[ "$package" == "pylon" &&
        "$architecture" == "arm64" &&
        "$version" == 26.06.2* ]]; then
    PYLON_DEB="$deb"
    break
  fi
done < <(find "$WORK_DIR" -type f -name '*.deb' -print0)

[[ -n "$PYLON_DEB" ]] || {
  echo "ERROR: pylon 26.06.2 ARM64 package not found." >&2
  exit 1
}

echo "Installing:"
dpkg-deb -f "$PYLON_DEB" Package Version Architecture

sudo apt-get update
sudo apt-get install -y "$PYLON_DEB"

dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' pylon

test -d /opt/pylon
dpkg -S /etc/udev/rules.d/69-basler-cameras.rules
sudo udevadm control --reload-rules

echo "PASS: pylon ARM64 installation completed."
