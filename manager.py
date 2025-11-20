"""
manager.py

Raspberry Pi side of the Automatic Chess Board.

New architecture:
  - Each Nano continuously scans its 4 multiplexers x 8 channels = 32 NFC readers
    and caches the latest 32 UIDs.
  - Pi asks each Nano for a block dump (CMD_GET_BLOCK) and reconstructs the
    full 8x8 physical_board by concatenating left/right halves.

Keeps original data definitions (UIDs, uid_to_piece, Board wrapper, detect_move).
Based on original manager.py / RasPi pseudocode. :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}
"""

import chess
import chess.engine
import time
import yaml

# Optional: uncomment if you want to use smbus2 for real I2C comms
# from smbus2 import SMBus, i2c_msg

PLAYER, COM = 0, 1
engine_path = None

TERMINAL_PLAY = True

try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        engine_path = config["path"]

except Exception as e:
    raise Exception("Must initialize config.yaml with path:path_to_stockfish_executable")



# -----------------------------
# I2C / Nano protocol constants
# -----------------------------
# Example I2C addresses for the two nanos (set to your actual addresses)
NANO_LEFT_ADDR = 0x10
NANO_RIGHT_ADDR = 0x11

CMD_GET_BLOCK = 0x01   # Pi -> Nano: request cached 32 UID block
CMD_PING      = 0x02   # Optional health check

UID_LEN = 7            # bytes per UID (your UID size)
NUM_READERS_PER_NANO = 32
BLOCK_BYTES = UID_LEN * NUM_READERS_PER_NANO  # expected bytes in response

# -------------------------------
# Helpers for board coordinates
# -------------------------------
def pos_to_uci(r, c):
    file = chr(ord("a") + c)
    rank = str(8 - r)
    return file + rank

# -------------------------------
# Move detection (unchanged style)
# -------------------------------
def detect_move(old_board, new_board):
    """
    Compare old vs new physical board states and return a UCI move.
    
    CURRENT IMPLEMENTATION:
        - Handles simple start→end movement (standard one-piece move)
        - Returns a UCI string, e.g. "e2e4"
    
    FUTURE EXTENSION POINT:
        - Detect promotion: return "e7e8q"
        - Detect castling: return "e1g1" or "e1c1"
        - Detect en passant: return appropriate UCI
        - Detect captures: same UCI (chess.Move handles it)
    
    Returns:
        uci_string or None
    """
    from_sq = None
    to_sq = None

    # Find moved piece (simple case)
    for r in range(8):
        for c in range(8):
            old = old_board[r][c]
            new = new_board[r][c]

            if old != new:
                if old is not None and new is None:
                    from_sq = (r, c)
                elif old is None and new is not None:
                    to_sq = (r, c)

    if from_sq and to_sq:
        fr, fc = from_sq
        tr, tc = to_sq
        return pos_to_uci(fr, fc) + pos_to_uci(tr, tc)

    return None

# -------------------------------
# Board wrapper (same style)
# -------------------------------
class Board(chess.Board):
    def __init__(self, fen=chess.STARTING_FEN, *, chess960=False):
        super().__init__(fen, chess960=chess960)

    def push(self, move, player=PLAYER):
        super().push(move)
        if player == COM:
            self.execute_motion(move)

    def execute_motion(self, move):
        # Hook for motion controller (electromagnet gantry)
        print("Motion controller would execute:", move)

# -------------------------------
# Game manager (updated read logic)
# -------------------------------
class GameManager:
    def __init__(self, engine_path="stockfish/stockfish"):
        self.board = Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.current_turn = PLAYER

        # 8×8 board holding UIDs (bytes) or None
        # This is the last recorded sweep
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        # Map UID -> chess.Piece ; fill with real data
        self.uid_to_piece = {}  # { bytes: chess.Piece(...) }

        # I2C bus object (optional real hardware)
        # self.bus = SMBus(1)

    # ---------------------------
    # Low-level I2C block read
    # ---------------------------
    def _i2c_read_block(self, nano_addr, cmd=CMD_GET_BLOCK, expected_len=BLOCK_BYTES, timeout=0.2):
        """
        Reads a cached block from a Nano.

        Returns:
            bytes object of length expected_len, or None on failure.
        Note:
            Replace the stub with actual smbus2 read logic when running on hardware.
        """
        # --- STUB / SIMULATOR ---
        # In simulation mode, return a bytes object of the right length filled with zeros
        # Replace this block with real I2C transaction using smbus2/i2c_msg:
        #
        # write = i2c_msg.write(nano_addr, [cmd])
        # read  = i2c_msg.read(nano_addr, expected_len)
        # self.bus.i2c_rdwr(write, read)
        # raw = bytes(list(read))
        #
        # For now return None to indicate "no hardware"
        print(f"[STUB] Requesting block from Nano 0x{nano_addr:02x}")
        return None
        # --- END STUB ---

    # ---------------------------
    # Parse returned block into 8x4 half-board
    # ---------------------------
    def _parse_halfblock(self, block_bytes):
        """
        block_bytes: bytes (expected length = UID_LEN * 32)
        Returns: 8x4 matrix (list of 8 rows, each row is list of 4 UID-or-None)
        UID representation: bytes of length UID_LEN. Empty reader -> all-zero UID or
        Nano should encode empty as all-zero; we map that to None.
        """
        if block_bytes is None:
            return None

        if len(block_bytes) != BLOCK_BYTES:
            # malformed read
            print("Malformed block length:", len(block_bytes))
            return None

        half = [[None for _ in range(4)] for _ in range(8)]
        for idx in range(NUM_READERS_PER_NANO):
            start = idx * UID_LEN
            raw_uid = block_bytes[start:start + UID_LEN]

            # convention: Nano uses b'\x00'*UID_LEN for empty/no-tag
            is_empty = all(b == 0 for b in raw_uid)
            uid = None if is_empty else bytes(raw_uid)

            # Map reader index -> (row, col) in 8x4
            # Define scanning order: idx 0..31 maps row-major 0..7 rows and 0..3 cols
            row = idx // 4
            col = idx % 4
            half[row][col] = uid

        return half

    # ---------------------------
    # Public API: read half-boards and assemble full board
    # ---------------------------
    def read_halfboard(self, nano_addr):
        """
        Returns 8x4 matrix of UIDs/None or None on failure.
        """
        block = self._i2c_read_block(nano_addr)
        return self._parse_halfblock(block)

    def assemble_full_board(self):
        """
        Requests both nanos and returns assembled 8x8 board (UIDs/None).
        Returns None if either read failed.
        """
        left = self.read_halfboard(NANO_LEFT_ADDR)
        if left is None:
            print("Failed to read left half")
            return None

        right = self.read_halfboard(NANO_RIGHT_ADDR)
        if right is None:
            print("Failed to read right half")
            return None

        full = [[None for _ in range(8)] for _ in range(8)]
        for r in range(8):
            # left 4 cols
            for c in range(4):
                full[r][c] = left[r][c]
            # right 4 cols (note: right half's col 0 maps to full col 4)
            for c in range(4):
                full[r][c + 4] = right[r][c]
        return full

    # ---------------------------
    # Move detection & game flow
    # ---------------------------
    def detect_player_move(self):
        new_state = self.assemble_full_board()
        if new_state is None:
            return None

        uci = detect_move(self.physical_board, new_state)
        if uci is None:
            return None

        self.physical_board = new_state
        return uci
    
    def get_player_move_from_input(self):
        """
        DEPRECATED — DO NOT USE. 
        This is preserved for compatibility with your original code.
        Use GameManager.detect_player_move() instead.
        """
        move = input("Enter a move in UCI format: ").strip()
        return move

    def get_ai_move(self):
        result = self.engine.play(self.board, chess.engine.Limit(time=0.1))
        return result.move

    def apply_move(self, move):
        self.board.push(move, self.current_turn)
        print(self.board)

    def player_move(self, move):
        if self.board.is_legal(move):
            self.board.push(move, self.current_turn)
            self.current_turn = COM
            print(self.board)
        else:
            print("Illegal move.")

    def ai_move(self):
        move = self.get_ai_move()
        self.board.push(move, self.current_turn)
        self.current_turn = PLAYER
        print(self.board)

    def is_game_over(self):
        return self.board.is_game_over()

    def play(self):
        print("Starting game with NFC board...")

        while not self.is_game_over():

            if self.current_turn == PLAYER:
                print("Waiting for player's physical move...")
                if TERMINAL_PLAY:
                    uci = self.get_player_move_from_input()
                else:
                    uci = self.detect_player_move()

                if uci is None:
                    # Either no move or read failure. Sweep again after short delay.
                    time.sleep(0.1)
                    continue

                try:
                    move = chess.Move.from_uci(uci)
                    self.player_move(move)
                except ValueError:
                    print(f"Detected invalid UCI move {uci}. Ignoring.")
                    continue

            else:
                print("Computer is thinking...")
                self.ai_move()

        print("Game over!")
        print("Result:", self.board.result())

    def quit(self):
        self.engine.quit()

# If executed as a script for testing:
if __name__ == "__main__":
    gm = GameManager(engine_path=engine_path)
    try:
        gm.play()
    finally:
        gm.quit()
