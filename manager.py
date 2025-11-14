import chess
import chess.engine
import yaml

# Constants for player types
PLAYER, COM = 0, 1

try:
    config = yaml.load("config.yaml")
    path_to_engine = config["path"]
except Exception as e:
    raise Exception("Must initialize config.yaml with path:path_to_stockfish_executable")

# -------------------------------
# NFC + Chess Board Integration
# -------------------------------
def pos_to_uci(r, c):
    """
    Convert matrix coordinates (0-7, 0-7) to UCI (a1..h8)
    Matrix uses r=0 as top, chess uses rank 8 at top.
    """
    file = chr(ord("a") + c)
    rank = str(8 - r)
    return file + rank


def detect_move(old_board, new_board):
    """
    Return (uid, (fr,fc), (tr,tc)) or None.
    Detects exactly 1 moved piece.
    """
    from_sq = None
    to_sq = None
    moved_uid = None

    for r in range(8):
        for c in range(8):
            old = old_board[r][c]
            new = new_board[r][c]

            if old != new:
                if old is not None and new is None:
                    from_sq = (r, c)
                    moved_uid = old

                elif old is None and new is not None:
                    to_sq = (r, c)

    if from_sq and to_sq and moved_uid:
        return moved_uid, from_sq, to_sq

    return None


class Board(chess.Board):
    def __init__(self, fen=chess.STARTING_FEN, *, chess960=False):
        super().__init__(fen, chess960=chess960)

    def push(self, move, player=PLAYER):
        super().push(move)
        if player == COM:
            self.execute_motion(move)

    def execute_motion(self, move):
        print("Motion controller now does something")

    def get_player_move_from_input(self):
        """
        DEPRECATED — DO NOT USE. 
        This is preserved for compatibility with your original code.
        Use GameManager.detect_player_move() instead.
        """
        move = input("Enter a move in UCI format: ").strip()
        return move


class GameManager:
    def __init__(self, engine_path=path_to_engine):
        self.board = Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.current_turn = PLAYER

        # 8×8 board holding UIDs or None
        # This is the last recorded sweep
        self.physical_board = [[None for _ in range(8)] for _ in range(8)]

        # Map UID → chess piece type/color (fill this with real data)
        self.uid_to_piece = {}  # { bytes: chess.Piece(...) }

    # ---------------------------
    # NFC interface layer
    # ---------------------------

    def read(self, spot):
        """
        Stub. You will replace this with the real NFC read function.
        Should return:
            UID as 7-byte bytes object, or
            None if square is empty
        """
        r, c = spot
        return None  # placeholder

    def read_full_board(self):
        """
        Reads all 64 squares and returns a new 8x8 array of UIDs/None.
        """
        board = [[None for _ in range(8)] for _ in range(8)]
        for r in range(8):
            for c in range(8):
                board[r][c] = self.read((r, c))
        return board

    # ---------------------------
    # Move detection logic
    # ---------------------------

    def detect_player_move(self):
        """
        Performs a sweep, compares against previous state,
        detects 1 moved piece, and returns a UCI string.
        """
        new_state = self.read_full_board()

        detected = detect_move(self.physical_board, new_state)
        if detected is None:
            return None  # No valid move found this sweep

        uid, (fr, fc), (tr, tc) = detected

        # Update stored physical state
        self.physical_board = new_state

        # Convert to UCI
        uci = pos_to_uci(fr, fc) + pos_to_uci(tr, tc)
        return uci

    # ---------------------------
    # Game flow
    # ---------------------------

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
                uci = self.detect_player_move()

                if uci is None:
                    print("No valid move detected. Sweeping again...")
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
