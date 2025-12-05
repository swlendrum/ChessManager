"""
manager.py — NFC chessboard manager (Nano0-only testing version)

Features added in this version:
  ✓ Uses Nano0 only — Nano1 remains disabled until hardware is connected.
  ✓ Full-board assembly still works, but right half uses dummy empty board.
  ✓ Startup reads physical board TWICE (or configurable count), ensuring stable init state.
  ✓ Move detection now uses LEGAL-MOVE FEN MATCHING:
        - For player's turn, precompute:
              { fen_after_move : move_uci }
        - Read new hardware board
        - Convert that board to FEN
        - If its FEN matches one of the legal move FENs → that's the move.
  ✓ Diff detection requires SAME new board state seen twice before accepting it.
  ✓ Keeps all old commented-out code; minimal changes elsewhere.
"""

import chess
import chess.engine
import time
import yaml

import serial
import serial.tools.list_ports


# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
PLAYER, COM = 0, 1
engine_path = None

TERMINAL_PLAY = False
STABILITY_COUNT = 2   # <-- number of times a board state must repeat to count as "real"


# ----------------------------------------------------------------------
# Load config.yaml for Stockfish path
# ----------------------------------------------------------------------
try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        engine_path = config["path"]

except Exception:
    raise Exception("Must initialize config.yaml with path:path_to_stockfish_executable")


# ----------------------------------------------------------------------
# Serial protocol constants
# ----------------------------------------------------------------------
CMD_GET_BLOCK = 0x01
CMD_PING      = 0x02

NUM_READERS_PER_NANO = 32
BLOCK_BYTES = NUM_READERS_PER_NANO

# Nano serials
NANO0_SERIAL = "A5069RR4"   # FOR TESTING: Using Nano0 as the only real Nano


# ----------------------------------------------------------------------
# Mapping arrays (raw index -> physical location remap)
# ----------------------------------------------------------------------
# Nano0 reads left half a–d
NANO0_MAP = [4, 5, 6, 7, 2, 3, 1, 0]

# Nano1 placeholder
NANO1_MAP = [4, 5, 2, 3, 1, 0, 6, 7]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
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
            line += ".  " if cell is None else f"{cell}  "
        print(line)

    print("\n    a  b  c  d  e  f  g  h\n")


# ----------------------------------------------------------------------
# Convert hardware 8×8 piece-ID matrix → FEN piece placement string
# ----------------------------------------------------------------------
PIECE_ID_TO_FEN = {
    1: "P", 2: "R", 3: "N", 4: "B", 5: "Q", 6: "K",
    7: "p", 8: "r", 9: "n", 10: "b", 11: "q", 12: "k"
}

def board_to_fen_piece_placement(board8x8):
    """
    Convert a matrix of ints/None into the piece-placement portion of a FEN string.
    """
    fen_rows = []
    for r in range(8):
        empty = 0
        row_str = ""
        for c in range(8):
            v = board8x8[r][c]
            if v is None:
                empty += 1
            else:
                if empty > 0:
                    row_str += str(empty)
                    empty = 0
                row_str += PIECE_ID_TO_FEN.get(v, "?")
        if empty > 0:
            row_str += str(empty)
        fen_rows.append(row_str)
    return "/".join(fen_rows)


# ----------------------------------------------------------------------
# SerialNano wrapper
# ----------------------------------------------------------------------
class SerialNano:
    def __init__(self, serial_number, baud=115200, timeout=0.3, label="Nano"):
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
            raise RuntimeError(
                f"{self.label}: Cannot find serial_number={self.serial_number}"
            )

        print(f"[{self.label}] Connecting on {port_name}")
        self.ser = serial.Serial(port_name, baudrate=self.baud, timeout=self.timeout)
        time.sleep(2.0)

    def ping(self):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_PING]))
            self.ser.flush()
            resp = self.ser.read(1)
            if resp:
                print(f"[{self.label}] Ping: {resp}")
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
                print(f"[{self.label}] get_block mismatch: got {len(data)} bytes")
                return None

            return list(data)

        except Exception as e:
            print(f"[{self.label}] get_block error:", e)
            return None

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# GameManager
# ----------------------------------------------------------------------
class GameManager:
    def __init__(self, engine_path="stockfish/stockfish"):
        self.board = chess.Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.current_turn = PLAYER

        # Physical board state (IDs or None)
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        self.nano0 = SerialNano(NANO0_SERIAL, label="Nano0")
        # self.nano1 = SerialNano(NANO1_SERIAL, label="Nano1")  # disabled

        # Initialize the board from hardware
        self.physical_board = self.get_stable_board_initial()

        print("\n=== INITIAL HARDWARE BOARD STATE ===")
        print_raw_board(self.physical_board)

    # -------------------------
    # Remap half-board
    # -------------------------
    def _remap_and_reshape_half(self, raw_block, map_arr):
        if raw_block is None:
            return None
        if len(raw_block) != 32:
            return None

        remapped = [0] * 32
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

        half.reverse()
        return half

    # -------------------------
    # Read halves
    # -------------------------
    def _read_half_from_nano0(self):
        if not self.nano0.ping():
            print("Nano0 ping failed")
            return None

        raw = self.nano0.get_block()
        if raw is None:
            return None

        return self._remap_and_reshape_half(raw, NANO0_MAP)

    # Dummy right half
    def _read_half_dummy(self):
        return [[None for _ in range(4)] for _ in range(8)]

    # -------------------------
    # Assemble full board
    # -------------------------
    def assemble_full_board(self):
        left = self._read_half_from_nano0()
        if left is None:
            return None

        right = self._read_half_dummy()   # ← placeholder for Nano1

        full = [[None for _ in range(8)] for _ in range(8)]

        for r in range(8):
            for c in range(4):
                full[r][c] = left[r][c]
            for c in range(4):
                full[r][c + 4] = right[r][c]

        return full

    # -------------------------
    # STABLE READ HELPERS
    # -------------------------
    def read_stable_board(self, required=STABILITY_COUNT):
        """
        Reads until the same board appears `required` times in a row.
        """
        last = None
        count = 0

        while True:
            b = self.assemble_full_board()
            if b is None:
                continue

            if last is not None and b == last:
                count += 1
                if count >= required:
                    return b
            else:
                count = 1
                last = b

            time.sleep(0.1)

    def get_stable_board_initial(self):
        print("Reading initial hardware board...")
        return self.read_stable_board(required=STABILITY_COUNT)

    # -------------------------
    # FEN conversion
    # -------------------------
    def board_to_fen(self, board):
        piece_part = board_to_fen_piece_placement(board)
        # Side to move always PLAYER at startup
        return piece_part + " w KQkq - 0 1"

    # -------------------------
    # Move detection using LEGAL MOVE FEN MATCHING
    # -------------------------
    def detect_player_move(self):
        # 1) Build mapping {fen_after_move : move_uci}
        legal_map = {}
        for move in self.board.legal_moves:
            temp = self.board.copy()
            temp.push(move)
            fen = temp.board_fen()  # piece placement only
            legal_map[fen] = move.uci()

        # 2) Detect any hardware change
        while True:
            new_board = self.read_stable_board(required=STABILITY_COUNT)
            fen_piece = board_to_fen_piece_placement(new_board)

            # If unchanged, keep waiting
            if new_board == self.physical_board:
                time.sleep(0.1)
                continue

            # 3) See if this corresponds to a legal move
            if fen_piece in legal_map:
                uci = legal_map[fen_piece]
                print(f"\nDetected legal move: {uci}")
                self.physical_board = new_board
                return uci

            print("Change detected but does NOT match any legal move.")
            # Continue waiting for stable valid change

    # -------------------------
    # Gameplay functions (unchanged)
    # -------------------------
    def get_ai_move(self):
        result = self.engine.play(self.board, chess.engine.Limit(time=0.1))
        return result.move

    def apply_move(self, move):
        self.board.push(move, PLAYER)
        print(self.board)

    def quit(self):
        self.engine.quit()
        try:
            self.nano0.close()
        except:
            pass


if __name__ == "__main__":
    gm = GameManager(engine_path=engine_path)

    while True:
        board = gm.assemble_full_board()
        if board is not None:
            print_raw_board(board)
        else:
            print("No board aquired.")
        time.sleep(1.5)
    # try:
    #     gm.play()
    # finally:
    #     gm.quit()