"""
Chess engine core, handling game state and move generation.

This module holds the board state, generates legal moves, applies and undoes
moves, and tracks special chess rules such as check, castling, en passant, and
promotion.

The move generator works in two phases:
1. Generate pseudo-legal moves for each piece.
2. Filter them through check logic so only legal moves remain.

The pygame UI uses the generated legal moves directly. It matches clicked
start/end squares against the legal move list and then calls `make_move()`
with the matching `Move` object.

This structure also supports future AI/search code: use `get_valid_moves()`
to expand nodes, `make_ai_move()` to traverse the tree.

Examples
--------
Usage example of the module:

>>> from chess import chess_engine
>>> gs = chess_engine.GameState()
>>> valid_moves = gs.get_valid_moves()
"""


from dataclasses import dataclass


@dataclass
class CastleRights:
    """
    Data wrapper for storing castling privileges at a specific game state.
    """

    def __init__(
        self,
        white_king_side: bool,
        white_queen_side: bool,
        black_king_side: bool,
        black_queen_side: bool
    ) -> None:
        """
        Initialize the castle rights log entry.

        Parameters
        ----------
        white_king_side : bool
            True if white can castle king-side.
        white_queen_side : bool
            True if white can castle queen-side.
        black_king_side : bool
            True if black can castle king-side.
        black_queen_side : bool
            True if black can castle queen-side.
        """
        self.white_king_side = white_king_side
        self.white_queen_side = white_queen_side
        self.black_king_side = black_king_side
        self.black_queen_side = black_queen_side


class Move:
    """
    Representation of a single chess move.

    Stores source and destination squares, move type constraints, and provides
    functions to convert movements into standard chess notation.
    """
    NORMAL = 'normal'
    EN_PASSANT = 'en_passant'
    CASTLE = 'castle'
    PROMOTION = 'promotion'

    # Dictionary to translate rows and cols to ranks and files of chess notation
    ROWS_TO_RANKS = {
        0: '8',
        1: '7',
        2: '6',
        3: '5',
        4: '4',
        5: '3',
        6: '2',
        7: '1',
    }
    COLS_TO_FILES = {
        0: 'a',
        1: 'b',
        2: 'c',
        3: 'd',
        4: 'e',
        5: 'f',
        6: 'g',
        7: 'h',
    }

    def __init__(
        self,
        start_sq: tuple[int, int],
        end_sq: tuple[int, int],
        board: list[list[str]],
        move_type: str = 'normal',
        promotion_piece: str = 'Q'
    ) -> None:
        """
        Initialize a Move object with its state and properties.

        Parameters
        ----------
        start_sq : tuple of int
            The starting coordinate (row, col) of the move.
        end_sq : tuple of int
            The destination coordinate (row, col) of the move.
        board : list of list of str
            The board array to extract piece information.
        move_type : str, optional
            The special classification of the move. Default is 'normal'.
        promotion_piece : str, optional
            The piece selected if a pawn promotes. Default is 'Q'.
        """
        self.start_row = start_sq[0]
        self.start_col = start_sq[1]
        self.end_row = end_sq[0]
        self.end_col = end_sq[1]

        self.piece_moved = board[self.start_row][self.start_col]
        self.piece_captured = board[self.end_row][self.end_col]

        self.move_type = move_type
        self.promotion_piece = promotion_piece

        if self.move_type == self.EN_PASSANT:
            self.piece_captured = 'bP' if self.piece_moved == 'wP' else 'wP'

        # Property storing ambiguity notation context if evaluated during UI rendering
        self.disambiguation = ''

    @classmethod
    def normal(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
        """
        Construct a standard normal move.

        Parameters
        ----------
        start_sq : tuple of int
            The starting coordinate (row, col).
        end_sq : tuple of int
            The destination coordinate (row, col).
        board : list of list of str
            The current board array.

        Returns
        -------
        Move
            A new Move instance classified as NORMAL.
        """
        return cls(start_sq, end_sq, board, move_type=cls.NORMAL)

    @classmethod
    def en_passant(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
        """
        Construct an en-passant capture move.

        Parameters
        ----------
        start_sq : tuple of int
            The starting coordinate (row, col).
        end_sq : tuple of int
            The destination coordinate (row, col).
        board : list of list of str
            The current board array.

        Returns
        -------
        Move
            A new Move instance classified as EN_PASSANT.
        """
        return cls(start_sq, end_sq, board, move_type=cls.EN_PASSANT)

    @classmethod
    def castle(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
        """
        Construct a castle move.

        Parameters
        ----------
        start_sq : tuple of int
            The starting coordinate (row, col).
        end_sq : tuple of int
            The destination coordinate (row, col).
        board : list of list of str
            The current board array.

        Returns
        -------
        Move
            A new Move instance classified as CASTLE.
        """
        """Construct a castling move."""
        return cls(start_sq, end_sq, board, move_type=cls.CASTLE)

    @classmethod
    def promotion(
            cls,
            start_sq: tuple[int, int],
            end_sq: tuple[int, int],
            board: list[list[str]],
            promotion_piece: str = 'Q'
    ) -> 'Move':
        """
        Construct a pawn promotion move.

        Parameters
        ----------
        start_sq : tuple of int
            The starting coordinate (row, col) of the promoting pawn.
        end_sq : tuple of int
            The destination coordinate (row, col) on the promotion rank.
        board : list of list of str
            The current 2D board array configuration.
        promotion_piece : str, optional
            The target chess piece character designated for the promotion
            (typically 'Q', 'R', 'B', or 'N'). Default is 'Q'.

        Returns
        -------
        Move
            A new Move instance explicitly classified as PROMOTION with the
            designated target piece type.
        """
        return cls(
            start_sq,
            end_sq,
            board,
            move_type=cls.PROMOTION,
            promotion_piece=promotion_piece,
        )

    def __eq__(self, other: object) -> bool:
        """
        Determine equality between this move and another object.

        Parameters
        ----------
        other : object
            The object to compare against.

        Returns
        -------
        bool
            True if the compared object is a matching Move, False otherwise.
        """
        if isinstance(other, Move):
            return (
                    self.start_row == other.start_row
                    and self.start_col == other.start_col
                    and self.end_row == other.end_row
                    and self.end_col == other.end_col
                    and self.move_type == other.move_type
                    and self.promotion_piece == other.promotion_piece
            )
        return False

    @property
    def is_pawn_promotion(self) -> bool:
        """
        Check if the move involves a pawn reaching the furthest rank and promoting.

        Returns
        -------
        bool
            True if the internal move type matches PROMOTION, False otherwise.
        """
        return self.move_type == self.PROMOTION

    @property
    def is_enpassant_move(self) -> bool:
        """
        Check if the move is a special en-passant diagonal pawn capture.

        Returns
        -------
        bool
            True if the internal move type matches EN_PASSANT, False otherwise.
        """
        return self.move_type == self.EN_PASSANT

    @property
    def is_castle_move(self) -> bool:
        """
        Check if the move is a castling maneuver involving both the king and a rook.

        Returns
        -------
        bool
            True if the internal move type matches CASTLE, False otherwise.
        """
        return self.move_type == self.CASTLE

    def get_chess_notation(self) -> str:
        """
        Construct the algebraic chess notation string for the move.
        Uses standard English letters (N, B, R, Q, K) to represent the
        moving piece.

        Returns
        -------
        str
            The algebraic notation representing the move executed.
        """
        if self.is_castle_move:
            notation = 'O-O' if self.end_col > self.start_col else 'O-O-O'
        else:
            notation = ''

            if self.piece_moved[1] != 'P':
                notation = self.piece_moved[1]
                # Append file or rank modifier if ambiguous targeting is found
                if self.disambiguation:
                    notation += self.disambiguation

            # Handling captures
            if self.piece_captured != '--':
                if self.piece_moved[1] == 'P':
                    notation += self.COLS_TO_FILES[self.start_col]
                notation += 'x'

            # Generate standard destination suffix
            notation += self._get_file_rank(self.end_row, self.end_col)

            if self.is_pawn_promotion:
                notation += '=' + self.promotion_piece

        # Append check or checkmate symbols
        if getattr(self, 'is_checkmate', False):
            notation += '#'
        elif getattr(self, 'is_check', False):
            notation += '+'

        return notation

    def _get_file_rank(self, row: int, col: int) -> str:
        """
        Helper to convert matrix coordinates to standard board notations.

        Parameters
        ----------
        row : int
            The row index.
        col : int
            The column index.

        Returns
        -------
        str
            The rank and file representation (e.g. 'e4').
        """
        return self.COLS_TO_FILES[col] + self.ROWS_TO_RANKS[row]


class GameState:
    """
    Store all information about the current state of the game.

    Determines valid moves at the current state and maintains a log of
    made moves, castling rights, and en-passant squares.
    """

    def __init__(self) -> None:
        """
        Initialize the game state, placing pieces on their starting squares.
        """
        self.board = [
            ['bR', 'bN', 'bB', 'bQ', 'bK', 'bB', 'bN', 'bR'],
            ['bP', 'bP', 'bP', 'bP', 'bP', 'bP', 'bP', 'bP'],
            ['--', '--', '--', '--', '--', '--', '--', '--'],
            ['--', '--', '--', '--', '--', '--', '--', '--'],
            ['--', '--', '--', '--', '--', '--', '--', '--'],
            ['--', '--', '--', '--', '--', '--', '--', '--'],
            ['wP', 'wP', 'wP', 'wP', 'wP', 'wP', 'wP', 'wP'],
            ['wR', 'wN', 'wB', 'wQ', 'wK', 'wB', 'wN', 'wR'],
        ]
        self.white_to_move = True
        self.move_functions = {
            'P': self._get_pawn_moves,
            'R': self._get_rook_moves,
            'B': self._get_bishop_moves,
            'N': self._get_knight_moves,
            'Q': self._get_queen_moves,
            'K': self._get_king_moves,
        }

        # Current Kings' location
        self.white_king_location = (7, 4)
        self.black_king_location = (0, 4)
        # Kings' home square
        self.WHITE_KING_HOME_SQUARE = (7, 4)
        self.BLACK_KING_HOME_SQUARE = (0, 4)

        # Tracking active pieces to optimize move generation
        self.white_pieces = set()
        self.black_pieces = set()
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece != '--':
                    if piece[0] == 'w':
                        self.white_pieces.add((row, col))
                    else:
                        self.black_pieces.add((row, col))

        self.in_check = False
        self.is_checkmate = False
        self.is_stalemate = False
        self.checks = []
        self.pins = {}

        # Each element stores a Move() object
        self.move_log = []

        # Squares where en-passant capture is possible
        self.enpassant_possible = ()
        self.enpassant_possible_log = [self.enpassant_possible]

        # Castling rights
        self.white_castle_king_side = True
        self.white_castle_queen_side = True
        self.black_castle_king_side = True
        self.black_castle_queen_side = True
        self.castle_rights_log = [
            CastleRights(
                self.white_castle_king_side,
                self.white_castle_queen_side,
                self.black_castle_king_side,
                self.black_castle_queen_side
            )
        ]

        # Variables tracking the 50-move rule status
        self.halfmove_clock = 0
        self.halfmove_clock_log = []

        # Variables tracking threefold repetition status
        self.state_counts = {}
        self.state_log = []

        # Hash and store the absolute initial state configuration
        initial_state = self.get_board_state()
        self.state_counts[initial_state] = 1
        self.state_log.append(initial_state)

    @property
    def friendly_color(self) -> str:
        """
        Get the color character of the player whose turn it is.

        Returns
        -------
        str
            'w' if it is white's turn, 'b' otherwise.
        """
        return 'w' if self.white_to_move else 'b'

    @property
    def enemy_color(self) -> str:
        """
        Get the color character of the opposing player.

        Returns
        -------
        str
            'b' if it is white's turn, 'w' otherwise.
        """
        return 'b' if self.white_to_move else 'w'

    def get_valid_moves(self, for_ai: bool = False) -> list['Move']:
        """
        Generate all legal moves in the current position.

        Calculates checks and pins to filter pseudo-legal moves, ensuring
        no move leaves the king in check.

        Returns
        -------
        list of Move
            A list containing all valid, legal moves.
        """
        moves = []
        self.in_check, self.pins, self.checks = self._check_pins_checks()
        king_row, king_col = (
            self.white_king_location if self.white_to_move else self.black_king_location
        )
        if self.in_check:
            if len(self.checks) == 1:  # Single check --> Block check, capture piece, or move King
                moves = self._get_all_possible_moves()
                check = self.checks[0]
                check_row, check_col = check[0], check[1]
                piece_checking = self.board[check_row][check_col]
                valid_squares = set()

                # If checking piece is a knight, block is impossible; must capture or move king
                if piece_checking[1] == 'N':
                    valid_squares = {(check_row, check_col)}
                else:
                    for i in range(1, 8):
                        # check[2] and check[3] --> Check's direction
                        valid_square = (
                            king_row + check[2] * i,
                            king_col + check[3] * i,
                        )
                        valid_squares.add(valid_square)
                        if valid_square == (check_row, check_col):  # Reach the checking piece
                            break

                # Remove moves that don't satisfy block, capture, or evade logic
                for i in range(len(moves) - 1, -1, -1):
                    # Move doesn't move the King
                    if moves[i].piece_moved[1] != 'K':
                        # Move doesn't block check or capture
                        if (moves[i].end_row, moves[i].end_col) not in valid_squares:
                            del moves[i]
            else:  # Double check --> King is forced to move
                self._get_king_moves(king_row, king_col, moves)
        else:  # Not in check
            moves = self._get_all_possible_moves()

        # Determine Checkmate or Stalemate statuses
        if len(moves) == 0:
            if self.in_check:
                self.is_checkmate = True
            else:
                self.is_stalemate = True
        else:
            self.is_checkmate = False
            self.is_stalemate = False

            # Enforce 50-move standard clock limits and threefold matching checks
            current_state = self.get_board_state()
            if self.halfmove_clock >= 100 or self.state_counts.get(current_state, 0) >= 3:
                self.is_stalemate = True
                moves = []  # Immediately cease operation and return empty list on draw

        # Resolve Ambiguous Notation (Bypassed entirely if generated for AI node processing)
        if not for_ai and len(moves) > 0:
            move_map = {}
            for move in moves:
                if move.piece_moved[1] != 'P':
                    key = (move.piece_moved, move.end_row, move.end_col)
                    if key not in move_map:
                        move_map[key] = []
                    move_map[key].append(move)

            for key, matching_moves in move_map.items():
                if len(matching_moves) > 1:
                    for move in matching_moves:
                        cols = [m.start_col for m in matching_moves]
                        if cols.count(move.start_col) == 1:
                            # Distinguished by file (e.g., Ndf3)
                            move.disambiguation = Move.COLS_TO_FILES[move.start_col]
                        else:
                            rows = [m.start_row for m in matching_moves]
                            if rows.count(move.start_row) == 1:
                                # Distinguished by rank (e.g., N1f3)
                                move.disambiguation = Move.ROWS_TO_RANKS[move.start_row]
                            else:
                                # Distinguished by both (extremely rare, e.g., Qd4d5)
                                move.disambiguation = (
                                    Move.COLS_TO_FILES[move.start_col] + Move.ROWS_TO_RANKS[move.start_row]
                                )

        # Double-check en-passant moves for hidden horizontal pins
        # Example: bR(a5) --- wP(f5) - bP(g5) --- wK(h5).
        for i in range(len(moves) - 1, -1, -1):
            if moves[i].move_type == Move.EN_PASSANT:
                self.make_move(moves[i], annotate=False)
                self.white_to_move = not self.white_to_move  # Temporarily switch turn back to evaluate check
                in_check, _, _ = self._check_pins_checks()
                self.white_to_move = not self.white_to_move  # Revert turn switch
                self.unmake_move()
                if in_check:
                    del moves[i]
        return moves

    def make_move(self, move: 'Move', annotate: bool = True) -> None:
        """
        Execute a chess move on the board and update the game state.

        This method handles piece movement, pawn promotion, en-passant,
        castling, king location tracking, and updates the internal
        collections of piece coordinates.

        Parameters
        ----------
        move : Move
            The Move object containing source, destination, and type information
            of the move to be executed.
        annotate : bool, optional
            If True, calculates check status for notation.
            Set to False during simulation/validation moves for performance.
        """
        # Cache the current half-move clock value before any mutations
        self.halfmove_clock_log.append(self.halfmove_clock)

        # Reset half-move clock on any pawn advance or active capture
        if move.piece_moved[1] == 'P' or move.piece_captured != '--':
            self.halfmove_clock = 0
        else:
            self.halfmove_clock += 1

        self.enpassant_possible_log.append(self.enpassant_possible)
        self.board[move.start_row][move.start_col] = '--'
        self.board[move.end_row][move.end_col] = move.piece_moved
        self.move_log.append(move)
        self.white_to_move = not self.white_to_move

        # Update King stored location
        if move.piece_moved == 'wK':
            self.white_king_location = (move.end_row, move.end_col)
        elif move.piece_moved == 'bK':
            self.black_king_location = (move.end_row, move.end_col)

        # Update castling rights and append to castle_rights_log
        self._update_castle_rights(move, record=True)

        # Handle pawn promotion
        if move.move_type == Move.PROMOTION:
            promoted_piece = move.promotion_piece if move.promotion_piece else 'Q'
            self.board[move.end_row][move.end_col] = move.piece_moved[0] + promoted_piece

        # Handle en-passant setup
        # If pawn moves two squares, the intermediate square becomes en-passant target
        if move.piece_moved[1] == 'P' and abs(move.start_row - move.end_row) == 2:
            self.enpassant_possible = ((move.start_row + move.end_row) // 2, move.end_col)
        else:
            self.enpassant_possible = ()

        # Execute en-passant capture
        if move.move_type == Move.EN_PASSANT:
            self.board[move.start_row][move.end_col] = '--'

        # Execute castling mechanics (moving the rook)
        if move.move_type == Move.CASTLE:
            if move.end_col - move.start_col == 2:  # King side
                # Move the Rook and empty the original square of it
                self.board[move.end_row][move.end_col - 1] = self.board[move.end_row][move.end_col + 1]
                self.board[move.end_row][move.end_col + 1] = '--'
            else: # Queen side
                self.board[move.end_row][move.end_col + 1] = self.board[move.end_row][move.end_col - 2]
                self.board[move.end_row][move.end_col - 2] = '--'

        # Update pieces' tracked squares list
        _is_white_moved = not self.white_to_move
        friendly_pieces = self.white_pieces if _is_white_moved else self.black_pieces
        enemy_pieces = self.black_pieces if _is_white_moved else self.white_pieces

        # Update squares of the moved piece
        friendly_pieces.remove((move.start_row, move.start_col))
        friendly_pieces.add((move.end_row, move.end_col))

        # Delete square of the captured piece (if any)
        if move.piece_captured != '--':
            if move.move_type == Move.EN_PASSANT:
                enemy_pieces.remove((move.start_row, move.end_col))
            else:
                enemy_pieces.remove((move.end_row, move.end_col))

        # Update tracked rook coordinates if castling
        if move.move_type == Move.CASTLE:
            if move.end_col - move.start_col == 2:  # King side
                # Remove Rook's old square and add its new square
                friendly_pieces.remove((move.end_row, move.end_col + 1))
                friendly_pieces.add((move.end_row, move.end_col - 1))
            else:  # Queen side
                friendly_pieces.remove((move.end_row, move.end_col - 2))
                friendly_pieces.add((move.end_row, move.end_col + 1))

        # Check analysis is bypassed if evaluate/simulation is requested by AI
        if annotate:
            in_check, _, _ = self._check_pins_checks()
            move.is_check = in_check

        # Log and increment the current board layout frequency for repetition check
        current_state = self.get_board_state()
        self.state_log.append(current_state)
        self.state_counts[current_state] = self.state_counts.get(current_state, 0) + 1

    def unmake_move(self) -> None:
        """
        Undo the last move made in the game.

        Restores the board, turn, castling rights, and internal tracking lists
        to their exact state before the previous move was executed.
        """
        if len(self.move_log) != 0:  # Ensure there is a move to unmake
            # Revert the current layout string frequency allocation
            current_state = self.state_log.pop()
            self.state_counts[current_state] -= 1
            if self.state_counts[current_state] == 0:
                del self.state_counts[current_state]

            # Revert the half-move clock back to its historical index
            self.halfmove_clock = self.halfmove_clock_log.pop()

            last_move = self.move_log.pop()
            self.board[last_move.start_row][last_move.start_col] = last_move.piece_moved
            self.board[last_move.end_row][last_move.end_col] = last_move.piece_captured
            self.white_to_move = not self.white_to_move

            # Revert king stored location
            if last_move.piece_moved == 'wK':
                self.white_king_location = (last_move.start_row, last_move.start_col)
            elif last_move.piece_moved == 'bK':
                self.black_king_location = (last_move.start_row, last_move.start_col)

            # Revert en-passant capture
            if last_move.move_type == Move.EN_PASSANT:
                self.board[last_move.end_row][last_move.end_col] = '--'
                # Place the captured pawn back
                self.board[last_move.start_row][last_move.end_col] = last_move.piece_captured

            # Restore previous en-passant state
            self.enpassant_possible = self.enpassant_possible_log.pop()

            # Restore previous castling rights
            self.castle_rights_log.pop()
            castle_rights = self.castle_rights_log[-1]
            self.white_castle_king_side = castle_rights.white_king_side
            self.white_castle_queen_side = castle_rights.white_queen_side
            self.black_castle_king_side = castle_rights.black_king_side
            self.black_castle_queen_side = castle_rights.black_queen_side

            # Undo rook movement for castling
            if last_move.move_type == Move.CASTLE:
                if last_move.end_col - last_move.start_col == 2:  # King side
                    # Move the Rook and empty the square where it was
                    self.board[last_move.end_row][last_move.end_col + 1] = (
                        self.board[last_move.end_row][last_move.end_col - 1]
                    )
                    self.board[last_move.end_row][last_move.end_col - 1] = '--'
                else:  # Queen side
                    self.board[last_move.end_row][last_move.end_col - 2] = (
                        self.board[last_move.end_row][last_move.end_col + 1]
                    )
                    self.board[last_move.end_row][last_move.end_col + 1] = '--'

            # Revert pieces' tracked squares list
            friendly_pieces = self.white_pieces if self.white_to_move else self.black_pieces
            enemy_pieces = self.black_pieces if self.white_to_move else self.white_pieces

            # Return moved piece to starting square
            friendly_pieces.remove((last_move.end_row, last_move.end_col))
            friendly_pieces.add((last_move.start_row, last_move.start_col))

            # Return captured piece (if any)
            if last_move.piece_captured != '--':
                if last_move.move_type == Move.EN_PASSANT:
                    enemy_pieces.add((last_move.start_row, last_move.end_col))
                else:
                    enemy_pieces.add((last_move.end_row, last_move.end_col))

            # Return rook to original square if it was a castle move
            if last_move.move_type == Move.CASTLE:
                if last_move.end_col - last_move.start_col == 2:  # King side
                    friendly_pieces.remove((last_move.end_row, last_move.end_col - 1))  # Remove new rook
                    friendly_pieces.add((last_move.end_row, last_move.end_col + 1))  # Return old rook
                else:  # Queen side
                    friendly_pieces.remove((last_move.end_row, last_move.end_col + 1))
                    friendly_pieces.add((last_move.end_row, last_move.end_col - 2))

    def get_board_state(self) -> tuple:
        """
        Generate a unique, immutable representation of the current board state.

        This method converts the mutable 2D board list into a nested tuple and
        combines it with the current game flags. Using tuples instead of string
        concatenation eliminates the overhead of continuous memory allocation
        during the AI's deep tree search, allowing the result to be used
        efficiently as a dictionary key for threefold repetition tracking.

        Returns
        -------
        tuple
            A hashable tuple containing the complete game state, structured as follows:
            - board_tuple (tuple of tuple of str): The 8x8 grid of chess pieces.
            - enpassant_possible (tuple of int): The (row, col) square where en passant
              is possible, or an empty tuple if not applicable.
            - white_castle_king_side (bool): True if White can castle king-side.
            - white_castle_queen_side (bool): True if White can castle queen-side.
            - black_castle_king_side (bool): True if Black can castle king-side.
            - black_castle_queen_side (bool): True if Black can castle queen-side.
            - white_to_move (bool): True if it is currently White's turn.
        """
        # Convert the mutable 2D list into an immutable tuple of tuples
        board_tuple = tuple(tuple(row) for row in self.board)

        return (
            board_tuple,
            self.enpassant_possible,
            self.white_castle_king_side,
            self.white_castle_queen_side,
            self.black_castle_king_side,
            self.black_castle_queen_side,
            self.white_to_move
        )

    def make_ai_move(self, move_tuple: tuple) -> tuple:
        """
        Execute a lightweight move specifically optimized for AI search trees.
        Avoids object instantiation and string evaluation to prevent GC pauses.

        Parameters
        ----------
        move_tuple : tuple
            Format: (start_row, start_col, end_row, end_col, move_type, promotion_piece)
            move_type mapping: 0=Normal, 1=Castle, 2=En Passant, 3=Promotion

        Returns
        -------
        tuple
            An undo package containing raw data to revert the state later.
            Format: (captured_piece, old_enpassant, old_castle_rights_tuple)
        """
        start_row, start_col, end_row, end_col, move_type, promo_piece = move_tuple

        piece_moved = self.board[start_row][start_col]
        captured_piece = self.board[end_row][end_col]

        # Pack castle rights into a simple boolean tuple to avoid object creation
        old_castle_rights = (
            self.white_castle_king_side,
            self.white_castle_queen_side,
            self.black_castle_king_side,
            self.black_castle_queen_side
        )
        old_enpassant = self.enpassant_possible

        # Update primary board state
        self.board[start_row][start_col] = '--'
        self.board[end_row][end_col] = piece_moved

        # Update King locations
        if piece_moved == 'wK':
            self.white_king_location = (end_row, end_col)
            self.white_castle_king_side = False
            self.white_castle_queen_side = False
        elif piece_moved == 'bK':
            self.black_king_location = (end_row, end_col)
            self.black_castle_king_side = False
            self.black_castle_queen_side = False

        # Handle specific move mechanics
        if move_type == 1:  # Castle
            if end_col - start_col == 2:  # King side
                self.board[end_row][end_col - 1] = self.board[end_row][end_col + 1]
                self.board[end_row][end_col + 1] = '--'
            else:  # Queen side
                self.board[end_row][end_col + 1] = self.board[end_row][end_col - 2]
                self.board[end_row][end_col - 2] = '--'

        elif move_type == 2:  # En Passant
            self.board[start_row][end_col] = '--'
            captured_piece = 'bP' if piece_moved == 'wP' else 'wP'

        elif move_type == 3:  # Promotion
            # Use string concatenation directly or map to predefined pieces for extra speed
            self.board[end_row][end_col] = piece_moved[0] + promo_piece

        # Update En Passant target square
        if piece_moved[1] == 'P' and abs(start_row - end_row) == 2:
            self.enpassant_possible = ((start_row + end_row) // 2, end_col)
        else:
            self.enpassant_possible = ()

        # Revoke Castling Rights if rooks are moved or captured
        if piece_moved[1] == 'R':
            if start_row == 7:
                if start_col == 0:
                    self.white_castle_queen_side = False
                elif start_col == 7:
                    self.white_castle_king_side = False
            elif start_row == 0:
                if start_col == 0:
                    self.black_castle_queen_side = False
                elif start_col == 7:
                    self.black_castle_king_side = False

        if captured_piece != '--' and captured_piece[1] == 'R':
            if end_row == 7:
                if end_col == 0:
                    self.white_castle_queen_side = False
                elif end_col == 7:
                    self.white_castle_king_side = False
            elif end_row == 0:
                if end_col == 0:
                    self.black_castle_queen_side = False
                elif end_col == 7:
                    self.black_castle_king_side = False

        self.white_to_move = not self.white_to_move

        return captured_piece, old_enpassant, old_castle_rights

    def unmake_ai_move(self, move_tuple: tuple, undo_package: tuple) -> None:
        """
        Reverse the state changes made by make_ai_move using the undo package.
        Operates entirely on primitives for maximum performance.

        Parameters
        ----------
        move_tuple : tuple
            The exact move tuple executed previously.
        undo_package : tuple
            The restoration data returned by make_ai_move.
        """
        start_row, start_col, end_row, end_col, move_type, promo_piece = move_tuple
        captured_piece, old_enpassant, old_castle_rights = undo_package

        self.white_to_move = not self.white_to_move
        piece_moved = self.board[end_row][end_col]

        # Restore the piece to its starting square
        if move_type == 3:  # Promotion (revert to Pawn)
            piece_moved = piece_moved[0] + 'P'
        self.board[start_row][start_col] = piece_moved

        # Restore the captured piece
        if move_type == 2:  # En Passant
            self.board[end_row][end_col] = '--'
            self.board[start_row][end_col] = captured_piece
        else:
            self.board[end_row][end_col] = captured_piece

        # Undo Rook movement for Castling
        if move_type == 1:
            if end_col - start_col == 2:  # King side
                self.board[end_row][end_col + 1] = self.board[end_row][end_col - 1]
                self.board[end_row][end_col - 1] = '--'
            else:  # Queen side
                self.board[end_row][end_col - 2] = self.board[end_row][end_col + 1]
                self.board[end_row][end_col + 1] = '--'

        # Restore King tracking
        if piece_moved == 'wK':
            self.white_king_location = (start_row, start_col)
        elif piece_moved == 'bK':
            self.black_king_location = (start_row, start_col)

        # Restore En Passant and Castling Rights precisely
        self.enpassant_possible = old_enpassant
        (self.white_castle_king_side,
         self.white_castle_queen_side,
         self.black_castle_king_side,
         self.black_castle_queen_side) = old_castle_rights

    def _get_all_possible_moves(self) -> list['Move']:
        """
        Generate all pseudo-legal moves without considering checks or pins.

        Returns
        -------
        list of Move
            A list containing all pseudo-legal moves.
        """
        possible_moves = []
        active_pieces = self.white_pieces if self.white_to_move else self.black_pieces
        for row, col in active_pieces:
            piece = self.board[row][col][1]  # Extract piece type: P, R, N, B, Q, K
            self.move_functions[piece](row, col, possible_moves)
            if piece == 'K':
                self._get_castle_moves(row, col, possible_moves)
        return possible_moves

    def _check_pins_checks(self) -> tuple[
        bool,
        dict[tuple[int, int], tuple[int, int]],
        list[tuple[int, int, int, int]]
    ]:
        """
        Scan outward from the king to identify active checks and absolute pins.

        Returns
        -------
        tuple
            A tuple containing three items:
            - in_check (bool): True if the king is currently in check.
            - pins (dict of tuple to tuple): Active pins mapping the pinned piece's
              coordinate (row, col) to its pinned vector direction (d_row, d_col).
            - checks (list of tuple): List of active checks storing the attacking
              piece's location and vector as (row, col, d_row, d_col).
        """
        pins = {}
        checks = []
        in_check = False
        row, col = self.white_king_location if self.white_to_move else self.black_king_location
        directions = (
            (-1, 0),
            (0, -1),
            (1, 0),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        )
        # Check for Rooks, Bishops, Pawns, and Queens checks
        for i in range(len(directions)):
            d = directions[i]
            possible_pins = () # Reset for each direction
            for j in range(1, 8):
                end_row = row + d[0] * j
                end_col = col + d[1] * j
                if self._is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]

                    # Ignore the moving phantom King to prevent false blocks
                    if end_piece[0] == self.friendly_color and end_piece[1] != 'K':
                        if len(possible_pins) == 0:  # 1st friendly piece could be pinned
                            possible_pins = (end_row, end_col, d[0], d[1])
                        else:  # 2nd friendly piece, so no pins or checks exist further out
                            break
                    elif end_piece[0] == self.enemy_color:
                        enemy_piece_type = end_piece[1]

                        # Five check conditions mapped to direction index:
                        # 0-3: Orthogonal (Rook)
                        # 4-7: Diagonal (Bishop)
                        # Pawns attacking diagonally 1 square away
                        # Queens attack on any vector
                        # Kings block their respective squares (distance == 1)
                        if (
                            (0 <= i <= 3 and enemy_piece_type == 'R')
                            or (4 <= i <= 7 and enemy_piece_type == 'B')
                            or (
                                j == 1
                                and (
                                        (self.enemy_color == 'b' and 4 <= i <= 5)
                                        or (self.enemy_color == 'w' and 6 <= i <= 7)
                                )
                                and enemy_piece_type == 'P'
                            )
                            or (enemy_piece_type == 'Q')
                            or (j == 1 and enemy_piece_type == 'K')
                        ):
                            if len(possible_pins) == 0:
                                in_check = True
                                checks.append((end_row, end_col, d[0], d[1]))
                                break
                            else:  # A friendly piece is shielding the king -> Pin
                                # Store in Dict with Key is (row, col), Value is (d_row, d_col)
                                pins[(possible_pins[0], possible_pins[1])] = (possible_pins[2], possible_pins[3])
                                break
                        else:  # Enemy piece does not threaten the king along this vector
                            break
                else: # Off board
                    break

        # Check for Knight checks
        knight_moves = (
            (-2, -1), (-2, 1),
            (-1, -2), (-1, 2),
            (1, -2), (1, 2),
            (2, -1), (2, 1),
        )
        for move in knight_moves:
            end_row = row + move[0]
            end_col = col + move[1]
            if self._is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece[0] == self.enemy_color and end_piece[1] == 'N':
                    in_check = True
                    checks.append((end_row, end_col, move[0], move[1]))
                    break

        return in_check, pins, checks

    def _is_square_attacked(self, row: int, col: int) -> bool:
        """
        Determine if a specific square is under attack by any enemy piece.
        Optimized for king move generation to avoid full pin/check calculations.

        Parameters
        ----------
        row : int
            The row index of the square to check.
        col : int
            The column index of the square to check.

        Returns
        -------
        bool
            True if the square is attacked by an enemy piece, False otherwise.
        """
        enemy_color = 'b' if self.white_to_move else 'w'
        friendly_color = 'w' if self.white_to_move else 'b'

        directions = (
            (-1, 0), (0, -1), (1, 0), (0, 1),  # Rook and Queen orthogonal vectors
            (-1, -1), (-1, 1), (1, -1), (1, 1)  # Bishop and Queen diagonal vectors
        )

        # Check outward for Rooks, Bishops, Queens, Pawns, and King
        for i in range(len(directions)):
            d = directions[i]
            for j in range(1, 8):
                end_row = row + d[0] * j
                end_col = col + d[1] * j
                if self._is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]

                    # Ignore our own King to allow ray casting through its original position
                    if end_piece[0] == friendly_color and end_piece[1] != 'K':
                        break
                    elif end_piece[0] == enemy_color:
                        enemy_piece_type = end_piece[1]

                        # Check orthogonal attacks (Rook/Queen)
                        if 0 <= i <= 3 and enemy_piece_type in ('R', 'Q'):
                            return True
                        # Check diagonal attacks (Bishop/Queen)
                        elif 4 <= i <= 7 and enemy_piece_type in ('B', 'Q'):
                            return True
                        # Check Pawn attacks (Distance of 1, specific diagonals)
                        elif j == 1 and enemy_piece_type == 'P':
                            if enemy_color == 'b' and 4 <= i <= 5:
                                return True
                            elif enemy_color == 'w' and 6 <= i <= 7:
                                return True
                        # Check enemy King attacks (Distance of 1 in any direction)
                        elif j == 1 and enemy_piece_type == 'K':
                            return True
                        else:
                            break  # Other enemy piece blocking the ray
                else:
                    break

        # Check Knight attacks
        knight_moves = (
            (-2, -1), (-2, 1), (-1, -2), (-1, 2),
            (1, -2), (1, 2), (2, -1), (2, 1)
        )
        for m in knight_moves:
            end_row = row + m[0]
            end_col = col + m[1]
            if self._is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece[0] == enemy_color and end_piece[1] == 'N':
                    return True

        return False

    def _get_pawn_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal moves for a pawn at the specified location.

        Parameters
        ----------
        row : int
            The current row index of the pawn.
        col : int
            The current column index of the pawn.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        move_amount = -1 if self.white_to_move else 1
        start_row = 6 if self.white_to_move else 1
        back_row = 0 if self.white_to_move else 7
        _is_back_row = row + move_amount == back_row

        # Check if piece is pinned
        piece_pinned = False
        pin_direction = ()
        if (row, col) in self.pins:
            piece_pinned = True
            pin_direction = self.pins[(row, col)]

        def _add_move(end_row: int, end_col: int) -> None:
            """
            Helper function to handle adding a normal move or 4 promotion moves.
            """
            if _is_back_row:
                for piece in ['Q', 'R', 'B', 'N']:
                    possible_moves.append(
                        Move.promotion(
                            (row, col),
                            (end_row, end_col),
                            self.board, promotion_piece=piece
                        )
                    )
            else:
                possible_moves.append(
                    Move.normal((row, col), (end_row, end_col), self.board)
                )

        # Move up 1 square
        if self.board[row + move_amount][col] == '--':
            if not piece_pinned or pin_direction == (-1, 0) or pin_direction == (1, 0):
                # Use the helper function for forward moves
                _add_move(row + move_amount, col)

                # Move up 2 squares from the starting position
                if row == start_row and self.board[row + 2 * move_amount][col] == '--':
                    possible_moves.append(
                        Move.normal((row, col), (row + 2 * move_amount, col), self.board)
                    )

        # Capture left and right
        for col_offset in [-1, 1]:
            new_col = col + col_offset
            if 0 <= new_col < 8:
                if not piece_pinned or pin_direction == (move_amount, col_offset):
                    end_piece = self.board[row + move_amount][new_col]
                    if end_piece[0] == self.enemy_color:
                        # Use the helper function for capture moves
                        _add_move(row + move_amount, new_col)

                    # Handle en-passant logic
                    if (row + move_amount, new_col) == self.enpassant_possible:
                        possible_moves.append(
                            Move.en_passant((row, col), (row + move_amount, new_col), self.board)
                        )

    def _get_rook_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal moves for a rook at the specified location.

        Parameters
        ----------
        row : int
            The current row index of the rook.
        col : int
            The current column index of the rook.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        self._get_sliding_moves(row, col, possible_moves, directions)

    def _get_bishop_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal moves for a bishop at the specified location.

        Parameters
        ----------
        row : int
            The current row index of the bishop.
        col : int
            The current column index of the bishop.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        directions = ((-1, -1), (1, 1), (1, -1), (-1, 1))
        self._get_sliding_moves(row, col, possible_moves, directions)

    def _get_queen_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal moves for a queen at the specified location.

        Parameters
        ----------
        row : int
            The current row index of the queen.
        col : int
            The current column index of the queen.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        self._get_rook_moves(row, col, possible_moves)
        self._get_bishop_moves(row, col, possible_moves)

    def _get_sliding_moves(
            self,
            row: int,
            col: int,
            possible_moves: list['Move'],
            directions: tuple[tuple[int, int], ...]
    ) -> None:
        """
        Helper method to generate moves for sliding pieces (Rook, Bishop, Queen).

        Parameters
        ----------
        row : int
            The starting row index.
        col : int
            The starting column index.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        directions : tuple of tuple of int
            The tuple specifying vector directions the piece can slide.
        """
        # Check if piece is pinned
        piece_pinned = False
        pin_direction = ()
        if (row, col) in self.pins:
            piece_pinned = True
            pin_direction = self.pins[(row, col)]

        for d in directions:
            end_row = row
            end_col = col
            while True:
                end_row += d[0]
                end_col += d[1]
                if self._is_on_board(end_row, end_col):
                    if (
                        not piece_pinned
                        or pin_direction == (d[0], d[1])
                        or pin_direction == (-d[0], -d[1])
                    ):
                        end_piece = self.board[end_row][end_col]
                        if end_piece == '--':  # Empty square
                            possible_moves.append(
                                Move.normal((row, col), (end_row, end_col), self.board)
                            )
                        elif end_piece[0] == self.enemy_color:  # Opponent piece
                            possible_moves.append(
                                Move.normal((row, col), (end_row, end_col), self.board)
                            )
                            break
                        else:  # Block by a friendly piece
                            break
                else:  # Off board
                    break

    def _get_knight_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal moves for a knight at the specified location.

        Parameters
        ----------
        row : int
            The current row index of the knight.
        col : int
            The current column index of the knight.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        moves = (
            (-2, -1), (-2, 1),
            (2, -1), (2, 1),
            (-1, -2), (1, -2),
            (-1, 2), (1, 2),
        )
        # Check if piece is pinned
        piece_pinned = False
        pin_direction = ()
        if (row, col) in self.pins:
            piece_pinned = True

        for move in moves:
            end_row = row + move[0]
            end_col = col + move[1]
            if self._is_on_board(end_row, end_col):
                if not piece_pinned:
                    end_piece = self.board[end_row][end_col]
                    if end_piece == '--' or end_piece[0] == self.enemy_color:
                        possible_moves.append(
                            Move.normal((row, col), (end_row, end_col), self.board)
                        )

    def _get_king_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal normal moves for a king at the specified location.
        Uses optimized is_square_attacked to validate safe squares.

        Parameters
        ----------
        row : int
            The current row index of the king.
        col : int
            The current column index of the king.
        possible_moves : list of Move
            The list to which generated moves will be appended.
        """
        row_moves = (-1, -1, -1, 0, 0, 1, 1, 1)
        col_moves = (-1, 0, 1, -1, 1, -1, 0, 1)

        for i in range(8):
            end_row = row + row_moves[i]
            end_col = col + col_moves[i]
            if self._is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece == '--' or end_piece[0] == self.enemy_color:
                    # Validate if the destination square is safe directly
                    if not self._is_square_attacked(end_row, end_col):
                        possible_moves.append(
                            Move.normal((row, col), (end_row, end_col), self.board)
                        )

    def _get_castle_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all legal castling moves for the king.

        Parameters
        ----------
        row : int
            The current row index of the king.
        col : int
            The current column index of the king.
        possible_moves : list of Move
            The list to which generated castling moves will be appended.
        """
        if self.white_to_move:
            if (  # White King side castling
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_king_side
                and self.board[7][5] == '--'
                and self.board[7][6] == '--'
                and self.board[7][7] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 5), (7, 6)]):
                    possible_moves.append(Move.castle((7, 4), (7, 6), self.board))
            if (  # White Queen side castling
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_queen_side
                and self.board[7][1] == '--'
                and self.board[7][2] == '--'
                and self.board[7][3] == '--'
                and self.board[7][0] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 3), (7, 2)]):
                    possible_moves.append(Move.castle((7, 4), (7, 2), self.board))
        else:
            if (  # Black King side castling
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_king_side
                and self.board[0][5] == '--'
                and self.board[0][6] == '--'
                and self.board[0][7] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 5), (0, 6)]):
                    possible_moves.append(Move.castle((0, 4), (0, 6), self.board))
            if (  # Black Queen side castling
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_queen_side
                and self.board[0][1] == '--'
                and self.board[0][2] == '--'
                and self.board[0][3] == '--'
                and self.board[0][0] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 3), (0, 2)]):
                    possible_moves.append(Move.castle((0, 4), (0, 2), self.board))

    def _squares_safe_for_castle(self, squares: list[tuple[int, int]]) -> bool:
        """
        Check if castling squares are free from enemy attacks.
        Utilizes the optimized is_square_attacked method without mutating king location.

        Parameters
        ----------
        squares : list of tuple of int
            The list of coordinate tuples the king moves through.

        Returns
        -------
        bool
            True if all squares are safe, False otherwise.
        """
        for square in squares:
            if self._is_square_attacked(square[0], square[1]):
                return False
        return True

    def _update_castle_rights(self, move: 'Move', record: bool = True) -> None:
        """
        Update castling privileges after a piece moves or is captured.

        Parameters
        ----------
        move : Move
            The move executed on the board.
        record : bool, optional
            Flag to indicate whether to record the new rights to the history log.
            Default is True.
        """
        if move.piece_moved == 'wK':
            self.white_castle_king_side = False
            self.white_castle_queen_side = False
        elif move.piece_moved == 'bK':
            self.black_castle_king_side = False
            self.black_castle_queen_side = False
        elif move.piece_moved == 'wR':
            if move.start_row == 7:
                if move.start_col == 0:
                    self.white_castle_queen_side = False
                elif move.start_col == 7:
                    self.white_castle_king_side = False
        elif move.piece_moved == 'bR':
            if move.start_row == 0:
                if move.start_col == 0:
                    self.black_castle_queen_side = False
                elif move.start_col == 7:
                    self.black_castle_king_side = False

        # Verify if a rook was captured and remove corresponding castling right
        if move.piece_captured == 'wR':
            if move.end_row == 7:
                if move.end_col == 0:
                    self.white_castle_queen_side = False
                elif move.end_col == 7:
                    self.white_castle_king_side = False
        elif move.piece_captured == 'bR':
            if move.end_row == 0:
                if move.end_col == 0:
                    self.black_castle_queen_side = False
                elif move.end_col == 7:
                    self.black_castle_king_side = False

        if record:
            self.castle_rights_log.append(
                CastleRights(
                    self.white_castle_king_side,
                    self.white_castle_queen_side,
                    self.black_castle_king_side,
                    self.black_castle_queen_side
                )
            )

    @staticmethod
    def _is_on_board(row: int, col: int) -> bool:
        """
        Check if a given set of coordinates is within the 8x8 board.

        Parameters
        ----------
        row : int
            The row index.
        col : int
            The column index.

        Returns
        -------
        bool
            True if coordinates are valid, False otherwise.
        """
        return 0 <= row < 8 and 0 <= col < 8