"""
motion.py — FINAL VERSION COMPATIBLE WITH MotionSystem.hpp

All commands now match the *ASCII float protocol* used by the Nano motion system.

Protocol from MotionSystem.hpp:

    MOTION_MOVE_ABS (0x10):
        <0x10><ASCII float x><space><ASCII float y><space><int useMag>

    MOTION_MOVE_REL (0x11):
        <0x11><ASCII float dx><space><ASCII float dy><space><int useMag>

    MOTION_EMAG_ON  (0x12)
    MOTION_EMAG_OFF (0x13)
    MOTION_GO_HOME  (0x14)

We do NOT open a new serial port — manager.py passes nano0.ser.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Protocol, runtime_checkable

# ----------------------------------------------------------
# Board geometry
# ----------------------------------------------------------

# SW: float = 50.0
SW: float = 57.15 # mm

BOARD_SIZE = 8


# ----------------------------------------------------------
# Basic tuple-like Pos
# ----------------------------------------------------------

@dataclass
class Pos:
    x: float
    y: float

    def as_tuple(self): return (self.x, self.y)
    def as_int_tuple(self): return (int(round(self.x)), int(round(self.y)))


MotionStep = Pos


@runtime_checkable
class SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...


# ----------------------------------------------------------
# Coordinate conversion
# ----------------------------------------------------------

def letter_to_x(letter: str) -> float:
    i = ord(letter.lower()) - ord("a")
    return (i + 0.5) * SW

def number_to_y(rank: int) -> float:
    return (rank - 0.5) * SW

def uci_square_to_pos(sq: str) -> Pos:
    file = sq[0]
    rank = int(sq[1])
    return Pos(letter_to_x(file), number_to_y(rank))

def uci_to_coords(uci: str) -> Tuple[Pos, Pos]:
    return uci_square_to_pos(uci[:2]), uci_square_to_pos(uci[2:4])


# ----------------------------------------------------------
# Corner helpers
# ----------------------------------------------------------

def square_adjacent_corners(p: Pos) -> List[Pos]:
    half = SW / 2
    return [
        Pos(p.x - half, p.y - half),
        Pos(p.x + half, p.y - half),
        Pos(p.x - half, p.y + half),
        Pos(p.x + half, p.y + half),
    ]

def find_first_corner(start: Pos, end: Pos) -> Pos:
    corners = square_adjacent_corners(start)
    return min(corners, key=lambda c: (c.x - end.x)**2 + (c.y - end.y)**2)

def find_last_corner(start: Pos, end: Pos) -> Pos:
    corners = square_adjacent_corners(end)
    return min(corners, key=lambda c: (c.x - start.x)**2 + (c.y - start.y)**2)

def find_closest_corners(start: Pos, end: Pos):
    return find_first_corner(start, end), find_last_corner(start, end)


# ----------------------------------------------------------
# Path generation
# ----------------------------------------------------------

def to_corner(start: Pos, corner: Pos) -> List[MotionStep]:
    steps = []
    dx = corner.x - start.x
    dy = corner.y - start.y
    if dy != 0: steps.append(Pos(0, dy))
    if dx != 0: steps.append(Pos(dx, 0))
    return steps

def from_corner(corner: Pos, end: Pos) -> List[MotionStep]:
    steps = []
    dy = end.y - corner.y
    dx = end.x - corner.x
    if dx != 0: steps.append(Pos(dx, 0))
    if dy != 0: steps.append(Pos(0, dy))
    return steps

def manhattan(start: Pos, end: Pos) -> List[MotionStep]:
    steps = []
    dx = end.x - start.x
    dy = end.y - start.y
    if dx != 0: steps.append(Pos(dx, 0))
    if dy != 0: steps.append(Pos(0, dy))
    return steps

def concat_steps(steps):
    if not steps:
        return steps
    
    merged = [steps[0]]
    changed = False

    for s in steps[1:]:
        last = merged[-1]
        if last.x != 0 and s.x != 0:      # merge horizontal
            merged[-1] = Pos(last.x + s.x, last.y)
            changed = True
        elif last.y != 0 and s.y != 0:    # merge vertical
            merged[-1] = Pos(last.x, last.y + s.y)
            changed = True
        else:
            merged.append(s)
    
    # Filter out zero-length steps
    merged = [s for s in merged if s.x != 0 or s.y != 0]
    
    # If we made changes and there are now fewer steps, recurse
    # (in case filtering created new adjacent same-direction pairs)
    if changed and len(merged) < len(steps):
        return concat_steps(merged)
    
    return merged

def relative_to_homing(pos: Pos) -> Pos:
    to_homing = (-2.5 * SW, 7.5 * SW)
    return Pos(pos.x - to_homing[0], pos.y - to_homing[1])


# ----------------------------------------------------------
# Main Planner
# ----------------------------------------------------------

def generate_motion_steps(uci: str):
    start, end = uci_to_coords(uci)
    first, last = find_closest_corners(start, end)

    steps = []
    steps += to_corner(start, first)
    steps += manhattan(first, last)
    steps += from_corner(last, end)

    return start, concat_steps(steps)


# ----------------------------------------------------------
# MOTION COMMAND ENCODING (ASCII FLOATS!)
# ----------------------------------------------------------

MOTION_MOVE_ABS = 0x10
MOTION_MOVE_REL = 0x11
MOTION_EMAG_ON  = 0x12
MOTION_EMAG_OFF = 0x13
MOTION_GO_HOME  = 0x14


def send_abs(port: SerialLike, pos: Pos, useMag: int = 0):
    """
    Send: 0x10 + "x y useMag\n"
    """
    x, y = pos.as_int_tuple()
    packet = f"{x} {y} {useMag}\n".encode()
    port.write(bytes([MOTION_MOVE_ABS]) + packet)
    port.flush()
    print("[motion] ABS:", packet.decode().strip())


def send_rel(port: SerialLike, step: Pos, useMag: int = 1):
    """
    Send: 0x11 + "dx dy useMag\n"
    """
    dx, dy = step.as_int_tuple()
    packet = f"{dx} {dy} {useMag}\n".encode()
    port.write(bytes([MOTION_MOVE_REL]) + packet)
    port.flush()
    print("[motion] REL:", packet.decode().strip())


def execute_uci_move(uci: str, port: SerialLike):
    """
    Execute full motion for a UCI move using the Nano ASCII-float protocol.
    """
    print(f"[motion] Executing: {uci}")

    start_abs, steps = generate_motion_steps(uci)
    start_rel = relative_to_homing(start_abs)

    # 1. Move ABSOLUTELY to start square center
    send_abs(port, start_rel, useMag=0)

    # 2. Perform relative moves
    for s in steps:
        send_rel(port, s, useMag=1)


if __name__ == "__main__":
    # Testing script
    uci = "e7e6"
    start_abs, steps = generate_motion_steps(uci)
    start_rel = relative_to_homing(start_abs)
    print("Start rel:", start_rel)
    for step in steps:
        print(step)