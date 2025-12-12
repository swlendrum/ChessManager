# ChessManager — Automatic Chess Board System

A Raspberry Pi-based automatic chess board that reads NFC-tagged pieces using Arduino Nanos and executes moves via an electromagnet-driven gantry system. The system plays against a Stockfish chess engine, detecting player moves in real-time and automatically moving the computer's pieces.

## Table of Contents

- [System Overview](#system-overview)
- [Architecture](#architecture)
- [Hardware Requirements](#hardware-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Key Components](#key-components)

---

## System Overview

The ChessManager system is a full-duplex chess-playing robot with:

1. **NFC Board Sensing**: Two Arduino Nanos (Nano0 and Nano1) scan 4 multiplexers each, reading 64 NFC reader tags total to detect piece positions across the 8×8 board.
2. **Move Detection**: Real-time polling detects when the player moves a piece by comparing stable board states.
3. **AI Integration**: Stockfish chess engine provides computer moves with 0.1-second time limit per move.
4. **Motion Execution**: An electromagnet-driven gantry system moves pieces via serial commands to a motion controller (Nano).
5. **Legal Move Validation**: Moves are validated against cached legal moves and fallback simulation.

---

## Architecture

### Hardware Structure

```
┌─────────────────────────────────────┐
│     Raspberry Pi 4B (Manager)       │
│  - Chess logic & game state         │
│  - Stockfish engine                 │
│  - Serial comm to Nanos             │
└─────────────────────────────────────┘
         │              │
    USB Serial      USB Serial
         │              │
    ┌────▼──────┐  ┌────▼──────┐
    │   Nano 0  │  │   Nano 1  │  ← NFC Reading (left/right halves)
    │ (A900DMBL)│  │ (95635...)│
    └────▲──────┘  └────▲──────┘
         │              │
    ┌────┴──────────────┴────┐
    │  4×Multiplexers (each) │  ← 8 NFC readers per mux
    │  64 total NFC readers  │
    └────────────────────────┘
         │
    ┌────▼──────────────────┐
    │   Chess Pieces        │
    │  (NFC-tagged)         │
    └───────────────────────┘
    
    ┌─────────────────────────────────┐
    │  Motion Controller (Nano)       │
    │  - Electromagnet driver         │
    │  - XY gantry control            │
    └─────────────────────────────────┘
         │
    ┌────▼──────────────────┐
    │  XY Gantry            │
    │  + Electromagnet      │
    └───────────────────────┘
```

### Software Architecture

| Module | Purpose |
|--------|---------|
| `manager.py` | Game orchestration, board state, Stockfish interface, Nano I/O |
| `motion.py` | Chess move → gantry motion planning (path generation, corner detection) |
| `config.yaml` | Configuration file with Stockfish binary path |

---

## Hardware Requirements

### Microcontrollers
- **Raspberry Pi 4B**: Game manager and orchestration
- **Arduino Nano × 2**: NFC board sensing (Nano0 for left half, Nano1 for right half) and motion control

### Sensors & Actuators
- **NFC Readers**: 64 total (2 Nanos x 4 multiplexers each × 8 channels each)
  - Scanned continuously for piece UIDs
- **Electromagnet**: Driven by motion controller for piece pickup
- **XY Gantry**: Stepper-motor-driven for (x, y) positioning
- **NFC-Tagged Pieces**: Chess pieces with NFC tags

### Board Geometry
- **Square size**: 57.15 mm or 2.25 inches
- **Board dimensions**: 8×8 = 57.15 × 8 = ~457 mm per side
- **Coordinate system**: Bottom-left is (0, 0); top-right is ~457, ~457 mm

---

## Installation

### 1. Clone and Dependencies

```bash
cd /Users/slendrum/Desktop/WPI/RBE/594/ChessManager
pip install -r requirements.txt
```

**requirements.txt** includes:
- `PyYAML` — config file parsing
- `chess` — Python chess library
- `pyserial` — USB serial communication

### 2. Create `config.yaml`

```yaml
path: /path/to/stockfish/stockfish-macos-m1-apple-silicon
```

Replace with your Stockfish binary path.

### 3. Flash Arduino Sketches

Upload firmware to the three Nanos.
Firmware can be found [here](https://github.com/CAP1Sup/RBE594-Nanos)

### 4. Set Nano Serial Numbers

Update `manager.py` constants with your Nano serial numbers:
```python
NANO0_SERIAL = "A900DMBL"     # left half NFC reader
NANO1_SERIAL = "95635333231351B0D151"  # right half NFC reader
```

---

## Configuration

### config.yaml

```yaml
path: /absolute/path/to/stockfish/binary
```

Must point to a valid Stockfish UCI engine binary.

### manager.py Constants

Adjust if your hardware differs:

```python
NANO0_SERIAL = "A900DMBL"              # USB serial of left Nano
NANO1_SERIAL = "95635333231351B0D151"  # USB serial of right Nano

NANO0_MAP = [4, 5, 6, 7, 2, 3, 1, 0]   # Reader channel reordering for Nano0
NANO1_MAP = [4, 5, 2, 3, 1, 0, 6, 7]   # Reader channel reordering for Nano1

ID_TO_SYMBOL = {...}  # Map piece IDs to chess symbols (P, R, N, B, Q, K, p, r, etc.)
```

### motion.py Constants

```python
SW = 57.15  # mm per square (adjust to your board size)
```

---

## Usage

### Full Game Loop

```bash
python3 manager.py
```

**Startup sequence:**
1. Connects to Nano0 and Nano1 via USB (waits 5 seconds for bootloader)
2. Detects initial board state (waits for stable reading)
3. Converts board to FEN and loads into Stockfish
4. Alternates player and computer turns:
   - **Player**: Waits for piece movement, detects via NFC polling
   - **Computer**: Stockfish calculates, motion system executes move

**Expected output:**
```
[Nano0] Connecting on /dev/tty.usbserial-XXXXX
Detecting initial board...
Initial board detected:
Board state (symbols):
8 | r  n  b  q  k  b  n  r
7 | p  p  p  p  p  p  p  p
6 | .  .  .  .  .  .  .  .
5 | .  .  .  .  .  .  .  .
4 | .  .  .  .  .  .  .  .
3 | .  .  .  .  .  .  .  .
2 | P  P  P  P  P  P  P  P
1 | R  N  B  Q  K  B  N  R

Waiting for player's move...
```

## Project Structure

```
ChessManager/
├── manager.py              # Game logic, board reading, serial I/O
├── motion.py               # Move planning (path, corners, motion commands)
├── config.yaml             # Configuration (Stockfish path, etc.)
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

---

## Key Components

### manager.py

**GameManager class:**
- `__init__()`: Initialize Stockfish, connect to Nanos
- `assemble_full_board()`: Read both Nanos, combine into 8×8 board
- `detect_player_move()`: Poll for board changes, match against legal moves
- `get_ai_move()`: Query Stockfish for next move
- `play()`: Main game loop (player turn ↔ computer turn)

**SerialNano class:**
- `_open_port()`: Find Nano by serial number and open USB connection
- `ping()`: Verify Nano is responsive
- `get_block()`: Request 32-byte block of piece UIDs from mux chain
- `close()`: Clean shutdown

### motion.py

**Path Planning:**
- `generate_motion_steps(uci)`: Convert UCI move to motion steps
- `find_closest_corners()`: Identify corner exits for smooth motion
- `concat_steps()`: Merge adjacent same-direction motions; filter zero-length; recursively re-merge

**Motion Commands:**
- `send_abs(port, pos, useMag)`: Absolute move to (x, y)
- `send_rel(port, step, useMag)`: Relative move by (dx, dy)
- `execute_uci_move(uci, port)`: Full sequence (absolute to start, relative steps)

---

## Troubleshooting

### Connection Issues

**Problem:** `RuntimeError: Nano0: No USB device with serial A900DMBL`

**Solution:** Find your Nano's actual serial number:
```bash
system_profiler SPUSBDataType | grep -A 5 "Arduino"
```
Update `NANO0_SERIAL` and `NANO1_SERIAL` in `manager.py`.

### Board Detection Fails

**Problem:** "No matching legal move for this new board state."

**Solution:**
1. Check NFC reader mappings in `NANO0_MAP` and `NANO1_MAP`
2. Verify piece IDs in `ID_TO_SYMBOL` match your hardware
3. Manually inspect `detect_player_move()` output and compare detected FEN to expected board

### Motion Inaccuracy

**Problem:** Pieces not landing on exact squares.

**Solution:**
1. Calibrate `SW` (square width) in `motion.py`
2. Verify gantry encoder calibration on Nano motion firmware
3. Check electromagnet timing and pickup force
4. Inspect corner detection in `find_closest_corners()`

### Stockfish Not Found

**Problem:** `Exception: Must initialize config.yaml with { path: stockfish_executable_path }`

**Solution:** Create `config.yaml` with correct path:
```bash
echo "path: your-path-to-stockfish" > config.yaml
```

---

## License

Educational project for RBE 594 at Worcester Polytechnic Institute (WPI).

---

## Authors

- **Student**: Sean Lendrum
- **Advisors**: Prof. Vincent Aloi

For questions, refer to inline code comments and the [python-chess documentation](https://python-chess.readthedocs.io/).
