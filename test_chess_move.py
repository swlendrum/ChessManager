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
import serial
import serial.tools.list_ports

from manager import NANO0_SERIAL
from motion import execute_uci_move

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
        # Execute the UCI move e3g4 using motion planner
        execute_uci_move("e3g4", port)
        # brief pause to allow commands to flush / device to react
        time.sleep(0.2)
        print("Executed UCI move e3g4.")

    finally:
        try:
            port.close()
            print("Closed serial port")
        except Exception:
            pass


if __name__ == "__main__":
    main()
