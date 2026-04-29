"""Detect the USB-to-serial adapter (VID/PID containing 1A86) and write the
result to printer_config.ini so other scripts can reference it."""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

import serial.tools.list_ports

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

TARGET_ID  = "1A86"                        # matches VID or PID in the hwid string
CONFIG_FILE = Path(__file__).parent / "printer_config.ini"

# Default serial settings for Epson receipt/label printers.
# Adjust baud/bits/parity/stop to match your printer's DIP-switch configuration.
DEFAULT_BAUD     = 9600
DEFAULT_BYTESIZE = 8      # data bits
DEFAULT_PARITY   = "N"    # N=none, E=even, O=odd
DEFAULT_STOPBITS = 1


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def find_target_port(target_id: str = TARGET_ID) -> str | None:
    """Return the first COM port whose hardware ID contains *target_id*."""
    needle = target_id.upper()
    for port in serial.tools.list_ports.comports():
        if needle in (port.hwid or "").upper():
            return port.device
    return None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    return cfg


def save_config(cfg: configparser.ConfigParser) -> None:
    with CONFIG_FILE.open("w") as fh:
        cfg.write(fh)
    print(f"[config] Written to {CONFIG_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[detect] Scanning serial ports for VID/PID {TARGET_ID} …")

    port = find_target_port()

    cfg = load_config()
    if "printer" not in cfg:
        cfg["printer"] = {}

    if port:
        print(f"[detect] Found adapter on {port}")
        cfg["printer"]["port"] = port
    else:
        print(
            f"[detect] WARNING: No port with ID {TARGET_ID} found.\n"
            "         Make sure the USB adapter is plugged in and drivers are installed.\n"
            "         The config file will be written with an empty port value.",
            file=sys.stderr,
        )
        cfg["printer"].setdefault("port", "")

    # Write serial settings only when they are absent (don't overwrite manual edits).
    cfg["printer"].setdefault("baud",     str(DEFAULT_BAUD))
    cfg["printer"].setdefault("bytesize", str(DEFAULT_BYTESIZE))
    cfg["printer"].setdefault("parity",   DEFAULT_PARITY)
    cfg["printer"].setdefault("stopbits", str(DEFAULT_STOPBITS))

    save_config(cfg)
    return 0 if port else 1


if __name__ == "__main__":
    sys.exit(main())
