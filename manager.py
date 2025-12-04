"""
manager.py

Raspberry Pi side of the Automatic Chess Board.

New architecture:
  - Each Nano continuously scans its 4 multiplexers x 8 channels = 32 NFC readers
    and caches the latest 32 *piece IDs* (1 byte per square, 0 = empty).
  - Pi asks each Nano for a block dump (CMD_GET_BLOCK) and reconstructs the
    full 8x8 physical_board by concatenating left/right halves.

Keeps original data definitions (Board wrapper, detect_move).
Based on original manager.py / RasPi pseudocode. :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}
"""

import chess
import chess.engine
import time
import yaml

import serial
import serial.tools.list_ports

PLAYER, COM = 0, 1
engine_path = None

TERMINAL_PLAY = False

try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        engine_path = config["path"]

except Exception as e:
    raise Exception("Must initialize config.yaml with path:path_to_stockfish_executable")


# -----------------------------------
# Serial / Nano protocol constants
# -----------------------------------
CMD_GET_BLOCK = 0x01   # Pi -> Nano: request cached 32-byte block
CMD_PING      = 0x02   # Pi -> Nano: health check / heartbeat

NUM_READERS_PER_NANO = 32          # 4 mux * 8 channels
BLOCK_BYTES          = NUM_READERS_PER_NANO  # 1 byte per reader (piece enum)

# Known Nano serial numbers (FTDI / USB‐UART)
# NOTE: Fill in Nano0 serial when known.
# NANO0_SERIAL = "REPLACE_WITH_NANO0_SERIAL"  # a–d files (left half from White's POV)
NANO1_SERIAL = "A5069RR4"                     # e–h files (right half from White's POV)

# Mapping arrays:
# map_arr[i] = index in the *remapped* array where raw index i should go.
# Applied per 8-channel block (0–7, 8–15, 16–23, 24–31).
#
# Nano0 (a–d):
# NANO0_MAP = [7, 6, 1, 0, 2, 3, 5, 4]
#
# Nano1 (e–h):
NANO1_MAP = [7, 6, 5, 4, 1, 0, 2, 3]


# -------------------------------
# Helpers for board coordinates
# -------------------------------
def pos_to_uci(r, c):
    file = chr(ord("a") + c)
    rank = str(8 - r)
    return file + rank

def print_half_board(half, file_labels=("a", "b", "c", "d")):
    """
    Pretty-print an 8×4 half-board of raw piece IDs.
    half: list of 8 rows, each row is 4 columns
    file_labels: tuple of 4 file letters ("a","b","c","d") or ("e","f","g","h")
    """
    print("\nHalf-board (raw IDs):\n")

    for r in range(8):
        rank = 8 - r
        row = half[r]
        line = f"{rank} | "

        for cell in row:
            if cell is None:
                line += ".  "
            else:
                line += f"{cell}  "

        print(line)

    # File labels underneath
    print("\n    " + "  ".join(file_labels) + "\n")

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
# Serial Nano communication
# -------------------------------
class SerialNano:
    """
    Represents a single Nano connected via USB serial.
    
    Identified by USB serial_number (FTDI / UART chip), not by /dev/ttyUSBx,
    so that port ordering / plug order doesn't matter.
    """
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
            # serial.tools.list_ports.ListPortInfo.serial_number
            if p.serial_number == self.serial_number:
                port_name = p.device
                break

        if port_name is None:
            raise RuntimeError(
                f"{self.label}: No serial device found with serial_number={self.serial_number}"
            )

        print(f"[{self.label}] Connecting on {port_name}")
        self.ser = serial.Serial(port_name, baudrate=self.baud, timeout=self.timeout)
        # Allow Nano bootloader reset delay
        time.sleep(2.0)

    def ping(self) -> bool:
        """
        Send PING and consider any single-byte response as success.
        (We don't assume a specific ACK value; Nano currently uses Commands::ACK.)
        """
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_PING]))
            self.ser.flush()

            resp = self.ser.read(1)
            if resp:
                print(f"[{self.label}] Ping response raw: {resp}")
                return True
            else:
                print(f"[{self.label}] Ping timeout/no response")
                return False
        except Exception as e:
            print(f"[{self.label}] Ping error:", e)
            return False

    def get_block(self, expected_len=BLOCK_BYTES):
        """
        Request a 32-byte block from this Nano.
        Returns a list of ints of length expected_len, or None on failure.
        """
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_GET_BLOCK]))
            self.ser.flush()

            data = self.ser.read(expected_len)
            if len(data) != expected_len:
                print(f"[{self.label}] get_block: expected {expected_len} bytes, got {len(data)}")
                return None

            return list(data)
        except Exception as e:
            print(f"[{self.label}] get_block error:", e)
            return None

    def close(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass


# -------------------------------
# Game manager (updated read logic)
# -------------------------------
class GameManager:
    def __init__(self, engine_path="stockfish/stockfish"):
        self.board = Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.current_turn = PLAYER

        # 8×8 board holding piece IDs (int) or None
        # This is the last recorded sweep
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        # Placeholder for future UID/enum mapping if needed
        self.uid_to_piece = {}  # { bytes_or_int: chess.Piece(...) }

        # --- Serial Nanos ---
        # Nano 0 (a–d files) — placeholder, commented until serial is known.
        # self.nano0 = SerialNano(NANO0_SERIAL, label="Nano0")

        # Nano 1 (e–h files)
        self.nano1 = SerialNano(NANO1_SERIAL, label="Nano1")

    # ---------------------------
    # Remap & reshape half-board
    # ---------------------------
    def _remap_and_reshape_half(self, raw_block, map_arr):
        """
        raw_block: list of 32 ints (piece IDs from Nano, 0 = empty).
        map_arr: 8-element list describing how each 8-channel block is permuted.
        
        Returns:
            8x4 matrix (rows 0..7, cols 0..3) of piece IDs or None.
            Rows are flipped so row 0 = rank 8, row 7 = rank 1.
        """
        if raw_block is None:
            return None
        if len(raw_block) != NUM_READERS_PER_NANO:
            print("Malformed raw_block length:", len(raw_block))
            return None

        # Apply mapping per 8-channel block
        remapped = [0] * NUM_READERS_PER_NANO
        for mux in range(4):
            base = mux * 8
            for i in range(8):
                old_idx = base + i
                new_idx = base + map_arr[i]
                remapped[new_idx] = raw_block[old_idx]

        # Now remapped is length 32; interpret as 8 rows x 4 cols
        half = [[None for _ in range(4)] for _ in range(8)]
        for idx, val in enumerate(remapped):
            row = idx // 4
            col = idx % 4
            # Interpret 0 as empty, nonzero as piece ID (int)
            half[row][col] = None if val == 0 else val

        # Flip rows so row 0 = chess rank 8, row 7 = chess rank 1
        half.reverse()
        return half

    # ---------------------------
    # Public API: read half-boards
    # ---------------------------
    def _read_half_from_nano1(self):
        """
        Read and parse right half (e-h files) from Nano1.
        """
        # Optionally ping first; if ping fails, bail.
        if not self.nano1.ping():
            print("Nano1 ping failed")
            return None

        raw = self.nano1.get_block()
        if raw is None:
            return None

        return self._remap_and_reshape_half(raw, NANO1_MAP)

    # Template for Nano0 (left half), left here commented until serial is known.
    #
    # def _read_half_from_nano0(self):
    #     """
    #     Read and parse left half (a–d files) from Nano0.
    #     """
    #     if not self.nano0.ping():
    #         print("Nano0 ping failed")
    #         return None
    #
    #     raw = self.nano0.get_block()
    #     if raw is None:
    #         return None
    #
    #     return self._remap_and_reshape_half(raw, NANO0_MAP)

    def assemble_full_board(self):
        """
        Requests both nanos and returns assembled 8x8 board (piece IDs or None).
        Returns None if required reads fail.

        Current state:
          - Right half (e-h) is read from Nano1.
          - Left half (a-d) is a placeholder until Nano0 serial is known.
        """
        # TODO: when Nano0 serial is known, uncomment this and remove placeholder.
        #
        # left = self._read_half_from_nano0()
        # if left is None:
        #     print("Failed to read left half (Nano0)")
        #     return None

        # Placeholder: left half is empty for now
        left = [[None for _ in range(4)] for _ in range(8)]

        right = self._read_half_from_nano1()
        if right is None:
            print("Failed to read right half (Nano1)")
            return None

        full = [[None for _ in range(8)] for _ in range(8)]
        for r in range(8):
            # left 4 cols (a–d)
            for c in range(4):
                full[r][c] = left[r][c]
            # right 4 cols (e–h)
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
        # Close serial ports cleanly
        try:
            # if hasattr(self, "nano0"):
            #     self.nano0.close()
            if hasattr(self, "nano1"):
                self.nano1.close()
        except Exception:
            pass


# If executed as a script for testing:
if __name__ == "__main__":
    gm = GameManager(engine_path=engine_path)

    while True:
        board = gm.assemble_full_board()
        print_half_board(board)
        time.sleep(5)
    # try:
    #     gm.play()
    # finally:
    #     gm.quit()
