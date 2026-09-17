# Raspberry Pi Ubuntu setup

Run from the repository root on a fresh Ubuntu installation.

## Sensor access

```bash
sudo apt update
sudo apt install -y python3 python3-serial python3-smbus python3-tk i2c-tools curl

grep -qxF 'dtparam=i2c_arm=on' /boot/firmware/config.txt || \
  echo 'dtparam=i2c_arm=on' | sudo tee -a /boot/firmware/config.txt
echo i2c-dev | sudo tee /etc/modules-load.d/i2c.conf
sudo modprobe i2c-dev

sudo usermod -aG dialout "$USER"
getent group i2c >/dev/null && sudo usermod -aG i2c "$USER"
sudo ./mcu/install_pico_udev.sh
sudo reboot
```

After reconnecting, verify the compass (`0x2c`) and USB serial devices:

```bash
i2cdetect -y 1
ls -l /dev/ttyUSB* /dev/ttyACM* /dev/serial/by-id/*
./scripts/test_compass.py --samples 3
./scripts/collect_rf_sweeps.py --band both --sweeps-per-band 1
```

## Pico flashing

```bash
mkdir -p "$HOME/.local/bin"
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | \
  BINDIR="$HOME/.local/bin" sh

ARDUINO="$HOME/.local/bin/arduino-cli"
"$ARDUINO" config init --overwrite
"$ARDUINO" config add board_manager.additional_urls \
  https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json
"$ARDUINO" core update-index
"$ARDUINO" core install rp2040:rp2040

./mcu/flash_pico.sh --build-only sketch_aid_sweep_180
```

## Combined ROS 2 bringup service

Build the workspace and install the system service:

```bash
cd ~/aid-drone-detection/ros2_ws
source /opt/ros/jazzy/setup.bash
./build.sh
cd ..
sudo ./scripts/install_aid_bringup_service.sh
```

The installer does not start or enable the service. Manage it explicitly:

```bash
sudo systemctl start aid-ros2-bringup
sudo systemctl stop aid-ros2-bringup
sudo systemctl restart aid-ros2-bringup
sudo systemctl status aid-ros2-bringup
sudo journalctl -fu aid-ros2-bringup
```

Enable or disable automatic startup:

```bash
sudo systemctl enable aid-ros2-bringup
sudo systemctl disable aid-ros2-bringup
```