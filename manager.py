"""
manager.py — FULL PLAY VERSION (Updated logic)

Raspberry Pi side of the Automatic Chess Board.

This version:
  - Nano0 (left half) live reading
  - Nano1 (right half) still dummy None
  - Detects physical move by polling every 1.5 sec
  - When a DIFFERENT board appears → wait 2 seconds → confirm stability
  - NEW stable board is printed using symbols and compared to cached legal-move FENs
"""

"""
HOW TO RUN:
If doesn't work normally, so this:
Run the code on the Pi
Wait for it to say Connteing to Usb001 or whatever
it will pause for 5 seconds
In those 5 seconds, you must double-tap reset on Nano while 
simulataneously unplugging and then replugging the 5V out to mux's

"""

import chess
import chess.engine
import time
import yaml
import serial
import serial.tools.list_ports

# from motion import execute_uci_move

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
# NANO0_SERIAL = "A5069RR4"     # left half of the board
NANO0_SERIAL = "A900DMBL"

NANO1_SERIAL = "95635333231351B0D151" # right half


# Mapping (per 8-channel block)
NANO0_MAP = [4, 5, 6, 7, 2, 3, 1, 0]

NANO1_MAP = [4, 5, 2, 3, 1, 0, 6, 7]
NANO1_MAP2 = [2, 3, 4, 5, 1, 0, 6, 7]

# --------------------------------------------------
# PRINTING HELPERS (now print SYMBOLS)
# --------------------------------------------------
ID_TO_SYMBOL = {
    1: "P", 2: "R", 3: "N", 4: "B", 5: "Q", 6: "K",
    7: "p", 8: "r", 9: "n",10: "b",11: "q",12: "k"
}

def pretty_symbol(cell):
    if cell is None:
        return "."
    return ID_TO_SYMBOL.get(cell, "?")


def print_pretty_board(board):
    print("\nBoard state (symbols):\n")
    for r in range(8):
        rank = 8 - r
        row = board[r]
        line = f"{rank} | "
        for cell in row:
            line += pretty_symbol(cell) + "  "
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
            return bool(resp)
        except:
            return False

    def get_block(self, expected_len=BLOCK_BYTES):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([CMD_GET_BLOCK]))
            self.ser.flush()
            data = self.ser.read(expected_len)
            if len(data) != expected_len:
                return None
            return list(data)
        except:
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

        # The last accepted 8×8 board
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        # Single active Nano (left half)
        self.nano0 = SerialNano(NANO0_SERIAL, label="Nano0")
        self.nano1 = SerialNano(NANO1_SERIAL, label="Nano1")

    # --------------------------------------------------
    # Apply mapping & convert 32-byte block → 8×4 matrix
    # --------------------------------------------------
    def _remap_and_reshape_half(self, raw_block, Nano0=True):
        if raw_block is None or len(raw_block) != NUM_READERS_PER_NANO:
            return None

        remapped = [0] * NUM_READERS_PER_NANO
        for mux in range(4):
            if Nano0:
                map_arr = NANO0_MAP
            else:
                if mux == 0:
                    map_arr = NANO1_MAP
                else:
                    map_arr = NANO1_MAP2
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
        return self._remap_and_reshape_half(raw, Nano0=True)
    
    # --------------------------------------------------
    # Read right half from Nano1
    # --------------------------------------------------
    def _read_half_from_nano1(self):
        if not self.nano1.ping():
            print("Nano1 ping failed")
            return None
        raw = self.nano1.get_block()
        if raw is None:
            return None
        return self._remap_and_reshape_half(raw, Nano0=False)

    # --------------------------------------------------
    # Convert half-boards → full 8×8 (right half dummy)
    # --------------------------------------------------
    def assemble_full_board(self):
        left = self._read_half_from_nano0()
        if left is None:
            return None
        
        right = self._read_half_from_nano1()
        if right is None:
            return None

        # right = [[None for _ in range(4)] for _ in range(8)]
        
        full = [[None for _ in range(8)] for _ in range(8)]

        for r in range(8):
            for c in range(4):
                full[r][c] = left[r][c]
            for c in range(4):
                full[r][c+4] = right[r][c]

        return full

    # --------------------------------------------------
    # FEN conversion
    # --------------------------------------------------
    def id_to_fen_symbol(self, pid):
        return ID_TO_SYMBOL.get(pid, "?")

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
        return "/".join(rows) + " w - - 0 1"

    # --------------------------------------------------
    # CACHE LEGAL MOVES
    # --------------------------------------------------
    def cache_legal_moves(self):
        self.cached_fens = {}
        for mv in self.board.legal_moves:
            temp = self.board.copy()
            temp.push(mv)
            fen = temp.fen().split(" ")[0]  # piece placement only
            self.cached_fens[fen] = mv.uci()

    # --------------------------------------------------
    # Detect physical move (new logic)
    # --------------------------------------------------
    def detect_player_move(self):
        time.sleep(1)
        new_board = self.assemble_full_board()
        if new_board is None or new_board == self.physical_board:
            return None

        time.sleep(1)
        confirm = self.assemble_full_board()
        if confirm is None or confirm != new_board:
            return None

        # NEW stable board detected
        print("\n========================")
        print("NEW STATE DETECTED:")
        print("========================\n")
        print_pretty_board(new_board)

        new_fen = self.board_to_fen(new_board).split(" ")[0]
        print("Detected FEN:", new_fen)

        if new_fen in self.cached_fens:
            uci = self.cached_fens[new_fen]
            self.physical_board = new_board
            return uci

        print("No matching legal move for this new board state.")
        return None

    # --------------------------------------------------
    # NEW FUNCTION: WAIT FOR PHYSICAL BOARD TO MATCH ENGINE MOVE
    # --------------------------------------------------
    def wait_until_physical_matches(self, expected_fen, poll_interval=1.0):
        """
        Poll Nano every second until the board_to_fen() matches expected_fen.
        """
        print("\nWaiting for physical board to match engine move...")

        expected = expected_fen.split(" ")[0]

        while True:
            b = self.assemble_full_board()
            if b is not None:
                fen_phys = self.board_to_fen(b).split(" ")[0]

                if fen_phys == expected:
                    print("Physical board now matches engine move.")
                    self.physical_board = b
                    print_pretty_board(b)
                    return

            time.sleep(poll_interval)

    # --------------------------------------------------
    # AI move
    # --------------------------------------------------
    def get_ai_move(self):
        result = self.engine.play(self.board, chess.engine.Limit(time=0.1))
        return result.move

    # --------------------------------------------------
    # Full gameplay loop
    # --------------------------------------------------
    def play(self):
        print("Detecting initial board...")

        while True:
            b1 = self.assemble_full_board()
            time.sleep(2)
            b2 = self.assemble_full_board()
            if b1 == b2 and b1 is not None:
                self.physical_board = b1
                print("\nInitial board detected:")
                print_pretty_board(b1)
                break

        init_fen = self.board_to_fen(b1)
        print("Initial FEN:", init_fen)

        try:
            self.board = chess.Board(init_fen)
        except Exception as e:
            print("ERROR loading initial FEN:", e)
            return

        self.current_turn = PLAYER

        while not self.board.is_game_over():

            if self.current_turn == PLAYER:
                print("\nWaiting for player's move...")
                self.cache_legal_moves()
                uci = self.detect_player_move()

                if not uci:
                    continue

                move = chess.Move.from_uci(uci)
                print("Player move:", move)
                self.board.push(move)
                print(self.board)
                self.current_turn = COM

            else:
                print("\nComputer thinking...")
                mv = self.get_ai_move()
                uci = mv.uci()
                print("Computer plays:", mv)

                # Send motion command sequence to Nano0 (motion controller)
                # NOTE: nano0 is the same device used for board reading;
                # we are just sending different command bytes here.
                
                # TODO: Re-enable
                # execute_uci_move(uci, self.nano0.ser)

                # Update internal board state
                self.board.push(mv)
                print(self.board)

                # NEW: Wait for motion controller / physical board to catch up
                expected_fen = self.board.fen()
                self.wait_until_physical_matches(expected_fen)

                self.current_turn = PLAYER

        print("\nGame over:", self.board.result())

    def quit(self):
        self.engine.quit()
        try:
            self.nano0.close()
        except:
            pass


# --------------------------------------------------
# MAIN
# --------------------------------------------------
if __name__ == "__main__":
    gm = GameManager(engine_path)

    gm.play()
    gm.quit()
