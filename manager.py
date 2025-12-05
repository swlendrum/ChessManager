"""
manager.py — FULL PLAY VERSION

Raspberry Pi side of the Automatic Chess Board.

Architecture:
  - Nano0 continuously scans 32 NFC sensors (4 mux × 8 channels)
    and caches 1 byte per square (piece ID or 0 for empty).
  - Nano1 is not connected yet → right half is dummy (None).
  - Pi polls the Nanos, remaps, reshapes, and builds an 8×8 piece-ID board.
  - detect_player_move() uses FEN matching across legal moves.
  - play() runs the full game with Stockfish.

This file includes ONLY Nano0 live support → Nano1 is dummy.
"""

import chess
import chess.engine
import time
import yaml

import serial
import serial.tools.list_ports


# --------------------------------------------------
# GLOBAL CONSTANTS
# --------------------------------------------------
PLAYER, COM = 0, 1
TERMINAL_PLAY = False

try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        engine_path = config["path"]
except Exception:
    raise Exception("Must initialize config.yaml with { path: stockfish_executable_path }")


# --------------------------------------------------
# Serial protocol constants (Nano firmware)
# --------------------------------------------------
CMD_GET_BLOCK = 0x01
CMD_PING      = 0x02

NUM_READERS_PER_NANO = 32
BLOCK_BYTES          = NUM_READERS_PER_NANO

# Only Nano0 is active for now
NANO0_SERIAL = "A5069RR4"     # <-- left half of the board

# Mapping (per 8-channel block)
NANO0_MAP = [4, 5, 6, 7, 2, 3, 1, 0]

# Placeholder mapping for Nano1 (not used yet)
NANO1_MAP = [4, 5, 2, 3, 1, 0, 6, 7]


# --------------------------------------------------
# Helper for printing raw board
# --------------------------------------------------
def pos_to_uci(r, c):
    file = chr(ord("a") + c)
    rank = str(8 - r)
    return file + rank


def print_raw_board(board):
    print("\nCurrent board state (raw IDs):\n")
    for r in range(8):
        rank = 8 - r
        row = board[r]
        line = f"{rank} | "
        for cell in row:
            line += (".  " if cell is None else f"{cell}  ")
        print(line)
    print("\n    a  b  c  d  e  f  g  h\n")


# --------------------------------------------------
# SerialNano — connects to Nano by USB serial_number
# --------------------------------------------------
class SerialNano:
    def __init__(self, serial_number, baud=115200, timeout=1.0, label="Nano"):
        self.serial_number = serial_number
        self.baud = baud
        self.timeout = timeout
        self.label = label
        self.ser = None
        self._open_port()

    def _open_port(self):
        port_name = None
        for p in serial.tools.list_ports.comports():
            if p.serial_number == self.serial_number:
                port_name = p.device
                break

        if port_name is None:
            raise RuntimeError(f"{self.label}: No USB device with serial {self.serial_number}")

        print(f"[{self.label}] Connecting on {port_name}")
        self.ser = serial.Serial(port_name, baudrate=self.baud, timeout=self.timeout)
        time.sleep(5.0)   # Nano bootloader reset delay

    def ping(self):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_PING]))
            self.ser.flush()

            resp = self.ser.read(1)
            if resp:
                print(f"[{self.label}] Ping OK: {resp}")
                return True
            print(f"[{self.label}] Ping timeout")
            return False
        except Exception as e:
            print(f"[{self.label}] Ping error:", e)
            return False

    def get_block(self, expected_len=BLOCK_BYTES):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_GET_BLOCK]))
            self.ser.flush()

            data = self.ser.read(expected_len)
            if len(data) != expected_len:
                print(f"[{self.label}] get_block: expected {expected_len}, got {len(data)}")
                return None

            return list(data)
        except Exception as e:
            print(f"[{self.label}] get_block error:", e)
            return None

    def close(self):
        try:
            self.ser.close()
        except:
            pass


# --------------------------------------------------
# GameManager
# --------------------------------------------------
class GameManager:
    def __init__(self, engine_path):
        self.board      = chess.Board()
        self.engine     = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.current_turn = PLAYER

        # This holds the most recently accepted 8×8 board
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        # Single active Nano (left half)
        self.nano0 = SerialNano(NANO0_SERIAL, label="Nano0")

    # --------------------------------------------------
    # Apply mapping & convert 32-byte block → 8×4 matrix
    # --------------------------------------------------
    def _remap_and_reshape_half(self, raw_block, map_arr):
        if raw_block is None or len(raw_block) != NUM_READERS_PER_NANO:
            print("Bad raw block")
            return None

        remapped = [0] * NUM_READERS_PER_NANO
        for mux in range(4):
            base = mux * 8
            for i in range(8):
                old_idx = base + i
                new_idx = base + map_arr.index(i)
                remapped[new_idx] = raw_block[old_idx]

        half = [[None for _ in range(4)] for _ in range(8)]
        for idx, val in enumerate(remapped):
            row = idx // 4
            col = idx % 4
            half[row][col] = None if val == 0 else val

        half.reverse()   # row0 = rank 8
        return half

    # --------------------------------------------------
    # Read left half from Nano0
    # --------------------------------------------------
    def _read_half_from_nano0(self):
        if not self.nano0.ping():
            print("Nano0 ping failed")
            return None

        raw = self.nano0.get_block()
        if raw is None:
            return None

        return self._remap_and_reshape_half(raw, NANO0_MAP)

    # --------------------------------------------------
    # Convert half-boards → full 8×8
    # Right half = dummy for now
    # --------------------------------------------------
    def assemble_full_board(self):
        left = self._read_half_from_nano0()
        if left is None:
            return None

        # Dummy right half (all empty)
        right = [[None for _ in range(4)] for _ in range(8)]

        full = [[None for _ in range(8)] for _ in range(8)]
        for r in range(8):
            for c in range(4):
                full[r][c] = left[r][c]
            for c in range(4):
                full[r][c+4] = right[r][c]

        return full

    # --------------------------------------------------
    # Stable board detection (debounce)
    # --------------------------------------------------
    def wait_for_stable_board(self, required_consistency=2):
        last = None
        count = 0

        while True:
            b = self.assemble_full_board()
            if b is None:
                continue

            if last is None:
                last = b
                count = 1
            elif b == last:
                count += 1
                if count >= required_consistency:
                    return b
            else:
                last = b
                count = 1
            
            time.sleep(2)

    # --------------------------------------------------
    # Board → FEN converter (for FEN matching)
    # --------------------------------------------------
    def board_to_fen(self, raw):
        rows = []
        for r in range(8):
            row = raw[r]
            fen_row = ""
            empties = 0

            for cell in row:
                if cell is None:
                    empties += 1
                else:
                    if empties > 0:
                        fen_row += str(empties)
                        empties = 0
                    fen_row += self.id_to_fen_symbol(cell)

            if empties > 0:
                fen_row += str(empties)

            rows.append(fen_row)

        fen = "/".join(rows) + " w - - 0 1"
        return fen

    # --------------------------------------------------
    # Convert piece ID → FEN char (YOUR mapping)
    # --------------------------------------------------
    def id_to_fen_symbol(self, pid):
        mapping = {
            1: "P", 2: "R", 3: "N", 4: "B", 5: "Q", 6: "K",
            7: "p", 8: "r", 9: "n",10: "b",11: "q",12: "k"
        }
        return mapping.get(pid, "?")

    # --------------------------------------------------
    # FEN matching across legal moves
    # --------------------------------------------------
    def match_board_to_legal_move(self, new_board):
        new_fen = self.board_to_fen(new_board)
        print("new fen:", new_fen)

        for mv in self.board.legal_moves:
            temp = self.board.copy()
            temp.push(mv)
            print("checking against move:", mv.uci(), "fen:", temp.fen())
            if temp.fen().split(" ")[0] == new_fen.split(" ")[0]:
                return mv.uci()

        return None

    # --------------------------------------------------
    # High-level: detect player's move
    # --------------------------------------------------
    def detect_player_move(self):
        new_board = self.wait_for_stable_board()
        if new_board is None:
            return None

        uci = self.match_board_to_legal_move(new_board)
        if uci:
            self.physical_board = new_board
            return uci

        return None

    # --------------------------------------------------
    # AI move (via Stockfish)
    # --------------------------------------------------
    def get_ai_move(self):
        result = self.engine.play(self.board, chess.engine.Limit(time=0.1))
        return result.move

    # --------------------------------------------------
    # Full gameplay loop
    # --------------------------------------------------
    def play(self):
        print("Starting game with NFC board...")
        print("Waiting to detect initial board...")

        init_b = self.wait_for_stable_board(required_consistency=2)
        self.physical_board = init_b

        print("Initial board detected:")
        print_raw_board(init_b)

        self.current_turn = PLAYER

        while not self.board.is_game_over():

            # ---------------- PLAYER MOVE ----------------
            if self.current_turn == PLAYER:
                print("Waiting for player's move...")
                uci = self.detect_player_move()
                if not uci:
                    time.sleep(1.5)
                    continue

                try:
                    move = chess.Move.from_uci(uci)
                    if move in self.board.legal_moves:
                        print(f"Player played: {uci}")
                        self.board.push(move)
                        print(self.board)
                        self.current_turn = COM
                    else:
                        print("Illegal move detected, ignoring.")

                except Exception:
                    print(f"Bad UCI string detected: {uci}")

            # ---------------- COMPUTER MOVE ----------------
            else:
                print("Computer thinking...")
                mv = self.get_ai_move()
                print("Computer plays:", mv)
                self.board.push(mv)
                print(self.board)
                self.current_turn = PLAYER

        print("Game over, result:", self.board.result())

    def quit(self):
        self.engine.quit()
        try:
            self.nano0.close()
        except:
            pass


# --------------------------------------------------
# MAIN (normal mode)
# --------------------------------------------------
if __name__ == "__main__":
    gm = GameManager(engine_path)

    # NORMAL OPERATION:
    # FULL GAMEPLAY LOOP (left half real, right half dummy)

    gm.play()

    gm.quit()
