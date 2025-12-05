#!/usr/bin/env python3
"""
test_motion.py — test Nano0 motion over a real serial port

This script opens a serial port (provided with --device) and sends two
relative motion commands to the Nano motion controller:
  1) REL +50 mm in X
  2) REL +50 mm in Y

Example:
  python3 test_motion.py --device /dev/tty.usbserial-0001 --baud 115200

If you want to run without hardware, pass `--simulate` to use a local mock.
"""

from __future__ import annotations
import argparse
import sys
import time

from motion import Pos, send_rel


class SerialMock:
    """Simple Serial-like mock for testing without hardware."""
    def __init__(self, name="nano0"):
        self.name = name
        self.writes = []

    def write(self, data: bytes) -> int:
        try:
            text = data[1:].decode(errors="replace")
        except Exception:
            text = ""
        print(f"[{self.name}] WRITE -> opcode=0x{data[0]:02x}, payload=({text.strip()}) | raw={data.hex()}")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        print(f"[{self.name}] FLUSH")


def open_real_serial(device: str, baud: int, timeout: float = 1.0):
    try:
        import serial
    except Exception as e:
        print("pyserial is required to use a real serial port. Install with: pip install pyserial")
        raise SystemExit(1) from e

    try:
        ser = serial.Serial(device, baudrate=baud, timeout=timeout)
        # ensure in non-blocking write mode
        ser.flush()
        print(f"Opened serial port {device} @ {baud} baud")
        return ser
    except Exception as e:
        print(f"Failed to open serial port {device}: {e}")
        raise


def open_by_serial_number(serial_number: str, baud: int, timeout: float = 1.0, label: str = "Nano"):
    """Find a serial device by its USB serial_number and open it.

    Mirrors the lookup logic used in `manager.SerialNano._open_port`.
    """
    try:
        import serial.tools.list_ports
    except Exception as e:
        print("pyserial is required to search serial ports. Install with: pip install pyserial")
        raise SystemExit(1) from e

    port_name = None
    for p in serial.tools.list_ports.comports():
        if p.serial_number == serial_number:
            port_name = p.device
            break

    if port_name is None:
        raise RuntimeError(f"{label}: No USB device with serial {serial_number}")

    print(f"[{label}] Connecting on {port_name}")
    ser = serial.Serial(port_name, baudrate=baud, timeout=timeout)
    # Match manager.py behavior: give device a short boot delay
    time.sleep(5.0)
    return ser


def parse_args():
    p = argparse.ArgumentParser(description="Test Nano0 motion via serial")
    p.add_argument("--device", "-d", help="Serial device path (e.g. /dev/ttyUSB0)")
    p.add_argument("--serial-number", "-s", help="USB serial_number of the Nano to connect to (preferred)")
    p.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate (default: 115200)")
    p.add_argument("--simulate", action="store_true", help="Use mock serial instead of real hardware")
    p.add_argument("--delay", type=float, default=0.2, help="Delay (s) between commands")
    return p.parse_args()


def main():
    args = parse_args()

    if args.simulate:
        port = SerialMock("nano0-mock")
    else:
        if not args.device:
            if not args.serial_number:
                print("Error: either --device or --serial-number is required when not using --simulate")
                sys.exit(2)
            try:
                port = open_by_serial_number(args.serial_number, args.baud, label="Nano0")
            except Exception as e:
                print(e)
                sys.exit(1)
        else:
            try:
                port = open_real_serial(args.device, args.baud)
            except Exception:
                sys.exit(1)

    try:
        # Send relative +50 mm in X
        send_rel(port, Pos(50.0, 0.0), useMag=1)
        time.sleep(args.delay)

        # Send relative +50 mm in Y
        send_rel(port, Pos(0.0, 50.0), useMag=1)
        time.sleep(args.delay)

        print("Commands sent. If using a real Nano, it should now be moving.")

    finally:
        # Close real serial port if applicable
        if not args.simulate:
            try:
                port.close()
                print("Closed serial port")
            except Exception:
                pass


if __name__ == "__main__":
    main()
