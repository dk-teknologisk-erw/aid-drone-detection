#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
FQBN="rp2040:rp2040:rpipico2:uploadmethod=picotool"
ARDUINO_CLI=${ARDUINO_CLI:-"$HOME/.local/bin/arduino-cli"}
BUILD_ONLY=false
SKETCH_ARGUMENT=""

usage()
{
    printf 'Usage: %s [--build-only] <sketch-name-or-path>\n' "$(basename "$0")"
}

pico_in_bootsel()
{
    local product
    for product in /sys/bus/usb/devices/*/product; do
        [[ -r "$product" ]] || continue
        [[ "$(<"$product")" == "RP2350 Boot" ]] && return 0
    done
    return 1
}

while (( $# > 0 )); do
    case "$1" in
        --build-only)
            BUILD_ONLY=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -* )
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ -n "$SKETCH_ARGUMENT" ]]; then
                printf 'Only one sketch may be specified.\n' >&2
                usage >&2
                exit 2
            fi
            SKETCH_ARGUMENT=$1
            ;;
    esac
    shift
done

if [[ -z "$SKETCH_ARGUMENT" ]]; then
    usage >&2
    exit 2
fi

if [[ -e "$SKETCH_ARGUMENT" ]]; then
    SKETCH_PATH=$SKETCH_ARGUMENT
elif [[ -e "$SCRIPT_DIR/$SKETCH_ARGUMENT" ]]; then
    SKETCH_PATH="$SCRIPT_DIR/$SKETCH_ARGUMENT"
else
    sketch_name=$(basename "$SKETCH_ARGUMENT")
    [[ "$sketch_name" == *.ino ]] || sketch_name="$sketch_name.ino"
    mapfile -d '' matches < <(find "$SCRIPT_DIR" -type f -name "$sketch_name" -print0)
    if (( ${#matches[@]} != 1 )); then
        printf 'Expected one sketch named %s, found %d.\n' "$sketch_name" "${#matches[@]}" >&2
        exit 1
    fi
    SKETCH_PATH=${matches[0]}
fi

if [[ -f "$SKETCH_PATH" ]]; then
    SKETCH_PATH=$(dirname -- "$SKETCH_PATH")
fi

if [[ ! -x "$ARDUINO_CLI" ]]; then
    printf 'Arduino CLI not found at %s\n' "$ARDUINO_CLI" >&2
    exit 1
fi

BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT
"$ARDUINO_CLI" compile --fqbn "$FQBN" --output-dir "$BUILD_DIR" "$SKETCH_PATH"

if [[ "$BUILD_ONLY" == true ]]; then
    exit 0
fi

picotool=$(find "$HOME/.arduino15/packages/rp2040/tools" -type f -name picotool -executable 2>/dev/null | sort -V | tail -n 1)
if [[ -z "$picotool" ]]; then
    printf '%s\n' 'Picotool was not found in the installed RP2040 Arduino core.' >&2
    exit 1
fi

shopt -s nullglob
uf2_files=("$BUILD_DIR"/*.uf2)
if (( ${#uf2_files[@]} != 1 )); then
    printf 'Expected one compiled UF2 image, found %d.\n' "${#uf2_files[@]}" >&2
    exit 1
fi

pico_ports=(/dev/serial/by-id/usb-Raspberry_Pi_Pico_2_*-if00)
if ! pico_in_bootsel && (( ${#pico_ports[@]} == 1 )); then
    if ! /bin/python3 - "${pico_ports[0]}" <<'PY'
import serial
import sys

port = serial.Serial(sys.argv[1], 1200, timeout=0.2)
try:
    try:
        port.dtr = False
    except BrokenPipeError:
        pass
finally:
    try:
        port.close()
    except BrokenPipeError:
        pass
PY
    then
        printf '%s\n' 'The Pico disconnected while requesting BOOTSEL; checking its new USB mode.' >&2
    fi

    for _ in {1..50}; do
        pico_in_bootsel && break
        read -r -t 0.1 _ || true
    done
fi

if ! pico_in_bootsel; then
    printf '%s\n' 'The Pico did not enter BOOTSEL mode.' >&2
    printf '%s\n' 'Run: sudo ./mcu/install_pico_udev.sh' >&2
    printf '%s\n' 'Reconnect the Pico while holding BOOTSEL, then run this command again.' >&2
    exit 1
fi

if ! "$picotool" load "${uf2_files[0]}" -x; then
    printf '%s\n' 'Picotool could not load the firmware in BOOTSEL mode.' >&2
    exit 1
fi