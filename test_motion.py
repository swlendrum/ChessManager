#!/usr/bin/env python3
"""
test_motion.py — test Nano0 motion (A900DMBL)

Connects to Nano0 (serial A900DMBL) and sends two relative motion commands:
  1) REL +50 mm in X
  2) REL +50 mm in Y
"""

from __future__ import annotations
import sys
import time
import serial.tools.list_ports

from motion import Pos, send_rel

NANO0_SERIAL = "A900DMBL"

def open_nano0(baud: int = 115200, timeout: float = 1.0):
    """Find and open Nano0 by its serial number."""
    port_name = None
    for p in serial.tools.list_ports.comports():
        if p.serial_number == NANO0_SERIAL:
            port_name = p.device
            break

    if port_name is None:
        raise RuntimeError(f"Nano0: No USB device with serial {NANO0_SERIAL}")

    print(f"[Nano0] Connecting on {port_name}")
    ser = serial.Serial(port_name, baudrate=baud, timeout=timeout)
    time.sleep(5.0)  # Boot delay
    return ser


def main():
    try:
        port = open_nano0()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    try:
        # Send relative +50 mm in X
        send_rel(port, Pos(50.0, 0.0), useMag=1)
        time.sleep(0.2)

        # Send relative +50 mm in Y
        send_rel(port, Pos(0.0, 50.0), useMag=1)
        time.sleep(0.2)

        print("Commands sent.")

    finally:
        try:
            port.close()
            print("Closed serial port")
        except Exception:
            pass


if __name__ == "__main__":
    main()
