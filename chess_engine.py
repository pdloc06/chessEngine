"""
Chess engine core, handling game state and move generation.

This module holds the board state, generates legal moves, applies and undoes
moves, and tracks special chess rules such as check, castling, en passant, and
promotion.

The move generator works in two phases:
1. Generate pseudo-legal moves for each piece.
2. Filter them through check logic so only legal moves remain.

The UI uses generated legal moves directly by matching clicked start/end squares
against the legal move list and calling `make_move()` with the matching `Move`.

This structure also supports future AI/search code: use `get_valid_moves(for_ai=True)`
to expand nodes, and `make_ai_move()` to traverse the tree efficiently.
"""

from dataclasses import dataclass


@dataclass
class CastleRights:
    """
    Data wrapper for storing castling privileges at a specific game state.

    Attributes
    ----------
    white_king_side : bool
        True if White can castle king-side.
    white_queen_side : bool
        True if White can castle queen-side.
    black_king_side : bool
        True if Black can castle king-side.
    black_queen_side : bool
        True if Black can castle queen-side.
    """
    white_king_side: bool
    white_queen_side: bool
    black_king_side: bool
    black_queen_side: bool


class Move:
    """
    Representation of a single chess move.

    Stores source and destination squares, move type constraints, and provides
    functions to convert movements into standard algebraic chess notation.
    """
    NORMAL = 'normal'
    EN_PASSANT = 'en_passant'
    CASTLE = 'castle'
    PROMOTION = 'promotion'

    # Translation dictionaries for chess notation
    ROWS_TO_RANKS = {0: '8', 1: '7', 2: '6', 3: '5', 4: '4', 5: '3', 6: '2', 7: '1'}
    COLS_TO_FILES = {0: 'a', 1: 'b', 2: 'c', 3: 'd', 4: 'e', 5: 'f', 6: 'g', 7: 'h'}

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
            # The captured piece in en passant is always the opposite color pawn
            self.piece_captured = 'bP' if self.piece_moved == 'wP' else 'wP'

        # Stores ambiguity notation context if evaluated during UI rendering
        self.disambiguation: str = ''

        self.is_check: bool = False
        self.is_checkmate: bool = False

    @classmethod
    def normal(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
        """Construct a standard normal move."""
        return cls(start_sq, end_sq, board, move_type=cls.NORMAL)

    @classmethod
    def en_passant(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
        """Construct an en-passant capture move."""
        return cls(start_sq, end_sq, board, move_type=cls.EN_PASSANT)

    @classmethod
    def castle(cls, start_sq: tuple[int, int], end_sq: tuple[int, int], board: list[list[str]]) -> 'Move':
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
        """Construct a pawn promotion move."""
        return cls(
            start_sq,
            end_sq,
            board,
            move_type=cls.PROMOTION,
            promotion_piece=promotion_piece,
        )

    def __eq__(self, other: object) -> bool:
        """Determine equality between this move and another object."""
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
        """Check if the move involves a pawn reaching the furthest rank and promoting."""
        return self.move_type == self.PROMOTION

    @property
    def is_enpassant_move(self) -> bool:
        """Check if the move is a special en-passant diagonal pawn capture."""
        return self.move_type == self.EN_PASSANT

    @property
    def is_castle_move(self) -> bool:
        """Check if the move is a castling maneuver involving both the king and a rook."""
        return self.move_type == self.CASTLE

    def get_chess_notation(self) -> str:
        """
        Construct the algebraic chess notation string for the move.

        Returns
        -------
        str
            The algebraic notation representing the move executed.
        """
        if self.is_castle_move:
            notation = 'O-O' if self.end_col > self.start_col else 'O-O-O'
        else:
            notation = ''

            # Non-pawn piece moves prefix the notation with their letter (N, B, R, Q, K)
            if self.piece_moved[1] != 'P':
                notation = self.piece_moved[1]
                if self.disambiguation:
                    notation += self.disambiguation

            # Append capture indicator
            if self.piece_captured != '--':
                if self.piece_moved[1] == 'P':
                    notation += self.COLS_TO_FILES[self.start_col]
                notation += 'x'

            # Append standard destination suffix
            notation += self._get_file_rank(self.end_row, self.end_col)

            if self.is_pawn_promotion:
                notation += '=' + self.promotion_piece

        # Append check or checkmate symbols (evaluated post-move)
        if self.is_checkmate:
            notation += '#'
        elif self.is_check:
            notation += '+'

        return notation

    def _get_file_rank(self, row: int, col: int) -> str:
        """Convert matrix coordinates to standard board notations (e.g., 'e4')."""
        return self.COLS_TO_FILES[col] + self.ROWS_TO_RANKS[row]


class GameState:
    """
    Stores all information about the current state of the game.

    Determines valid moves at the current state and maintains a log of
    made moves, castling rights, and en-passant squares.
    """

    def __init__(self) -> None:
        """Initialize the game state, placing pieces on their starting squares."""
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

        # Current and home Kings' locations
        self.white_king_location = (7, 4)
        self.black_king_location = (0, 4)
        self.WHITE_KING_HOME_SQUARE = (7, 4)
        self.BLACK_KING_HOME_SQUARE = (0, 4)

        # Track active piece coordinates to optimize move generation
        self.white_pieces: set[tuple[int, int]] = set()
        self.black_pieces: set[tuple[int, int]] = set()
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece != '--':
                    if piece[0] == 'w':
                        self.white_pieces.add((row, col))
                    else:
                        self.black_pieces.add((row, col))

        # Game state flags
        self.in_check: bool = False
        self.is_checkmate: bool = False
        self.is_stalemate: bool = False
        self.checks: list[tuple[int, int, int, int]] = []
        self.pins: dict[tuple[int, int], tuple[int, int]] = {}

        self.move_log: list[Move] = []

        # En-passant coordinates
        self.enpassant_possible: tuple[int, int] | None = None
        self.enpassant_possible_log: list[tuple[int, int] | None] = [self.enpassant_possible]

        # Castling rights mapping
        self.white_castle_king_side: bool = True
        self.white_castle_queen_side: bool = True
        self.black_castle_king_side: bool = True
        self.black_castle_queen_side: bool = True
        self.castle_rights_log: list[CastleRights] = [
            CastleRights(
                self.white_castle_king_side,
                self.white_castle_queen_side,
                self.black_castle_king_side,
                self.black_castle_queen_side
            )
        ]

        # Rule tracking logs
        self.halfmove_clock: int = 0
        self.halfmove_clock_log: list[int] = []
        self.state_counts: dict[tuple, int] = {}
        self.state_log: list[tuple] = []

        # Hash and store the absolute initial state configuration
        initial_state: tuple = self.get_board_state()
        self.state_counts[initial_state] = 1
        self.state_log.append(initial_state)

    @property
    def friendly_color(self) -> str:
        """Get the color character of the player whose turn it is."""
        return 'w' if self.white_to_move else 'b'

    @property
    def enemy_color(self) -> str:
        """Get the color character of the opposing player."""
        return 'b' if self.white_to_move else 'w'

    def get_valid_moves(self, for_ai: bool = False) -> list['Move'] | list[tuple[int, int, int, int, int]]:
        """
        Generate all legal moves in the current position.

        Calculates checks and pins to filter pseudo-legal moves, ensuring
        no move leaves the king in check. Returns either Move objects for UI
        or lightweight tuples for AI processing.

        Parameters
        ----------
        for_ai : bool, optional
            Flag to toggle output format. If False, returns Move objects. If
            True, returns lightweight 5-element tuples optimized for AI.
        """
        moves = []
        self.in_check, self.pins, self.checks = self._check_pins_checks()
        king_row, king_col = (
            self.white_king_location if self.white_to_move else self.black_king_location
        )

        if self.in_check:
            if len(self.checks) == 1:  # Single check -> Block, capture, or evade
                moves = self._get_all_possible_moves(for_ai=for_ai)
                check = self.checks[0]
                check_row, check_col = check[0], check[1]
                piece_checking = self.board[check_row][check_col]
                valid_squares = set()

                # If checking piece is a knight, block is impossible; must capture or move king
                if piece_checking[1] == 'N':
                    valid_squares = {(check_row, check_col)}
                else:
                    for i in range(1, 8):
                        valid_square = (king_row + check[2] * i, king_col + check[3] * i)
                        valid_squares.add(valid_square)
                        if valid_square == (check_row, check_col):
                            break

                # Remove moves that don't block the check, capture the checker, or move the king
                for i in range(len(moves) - 1, -1, -1):
                    if for_ai:
                        start_row, start_col, end_row, end_col, _ = moves[i]
                        if self.board[start_row][start_col][1] != 'K':
                            if (end_row, end_col) not in valid_squares:
                                del moves[i]
                    else:
                        if moves[i].piece_moved[1] != 'K':
                            if (moves[i].end_row, moves[i].end_col) not in valid_squares:
                                del moves[i]
            else:  # Double check -> King is strictly forced to move
                self._get_king_moves(king_row, king_col, moves, for_ai=for_ai)
        else:
            moves = self._get_all_possible_moves(for_ai=for_ai)

        # Evaluate Checkmate or Stalemate statuses
        if len(moves) == 0:
            if self.in_check:
                self.is_checkmate = True
            else:
                self.is_stalemate = True
        else:
            self.is_checkmate = False
            self.is_stalemate = False

            # Draw conditions (50-move limit or 3-fold repetition)
            current_state = self.get_board_state()
            if self.halfmove_clock >= 100 or self.state_counts.get(current_state, 0) >= 3:
                self.is_stalemate = True
                moves = []

        # Calculate Ambiguous Notation mapping for UI
        if not for_ai and len(moves) > 0:
            move_map: dict[tuple, list[Move]] = {}
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
                            move.disambiguation = Move.COLS_TO_FILES[move.start_col]
                        else:
                            rows = [m.start_row for m in matching_moves]
                            if rows.count(move.start_row) == 1:
                                move.disambiguation = Move.ROWS_TO_RANKS[move.start_row]
                            else:
                                move.disambiguation = (
                                    Move.COLS_TO_FILES[move.start_col] + Move.ROWS_TO_RANKS[move.start_row]
                                )

        # Filter out invalid En Passant moves that expose a horizontal pin
        for i in range(len(moves) - 1, -1, -1):
            if for_ai:
                if moves[i][4] == 2:  # En Passant move type index
                    undo_package = self.make_ai_move(moves[i])
                    # make_ai_move switches the turn to the enemy. We must switch it back
                    # momentarily to check if our own king is in check.
                    self.white_to_move = not self.white_to_move
                    in_check, _, _ = self._check_pins_checks()
                    # Revert the turn back before unmaking the move entirely
                    self.white_to_move = not self.white_to_move
                    self.unmake_ai_move(moves[i], undo_package)
                    if in_check:
                        del moves[i]
            else:
                if moves[i].move_type == Move.EN_PASSANT:
                    self.make_move(moves[i], annotate=False)
                    self.white_to_move = not self.white_to_move
                    in_check, _, _ = self._check_pins_checks()
                    self.white_to_move = not self.white_to_move
                    self.unmake_move()
                    if in_check:
                        del moves[i]

        return moves

    def make_move(self, move: 'Move', annotate: bool = True) -> None:
        """
        Execute a chess move on the board and update the game state.

        Parameters
        ----------
        move : Move
            The Move object containing the details of the play to be executed.
        annotate : bool, optional
            If True, calculates check status for algebraic notation. Set to
            False during evaluation simulations to boost performance.
        """
        self.halfmove_clock_log.append(self.halfmove_clock)

        # Reset clock on pawn advances or active captures
        if move.piece_moved[1] == 'P' or move.piece_captured != '--':
            self.halfmove_clock = 0
        else:
            self.halfmove_clock += 1

        self.enpassant_possible_log.append(self.enpassant_possible)
        self.board[move.start_row][move.start_col] = '--'
        self.board[move.end_row][move.end_col] = move.piece_moved
        self.move_log.append(move)
        self.white_to_move = not self.white_to_move

        # Maintain king location caches
        if move.piece_moved == 'wK':
            self.white_king_location = (move.end_row, move.end_col)
        elif move.piece_moved == 'bK':
            self.black_king_location = (move.end_row, move.end_col)

        self._update_castle_rights(move, record=True)

        if move.move_type == Move.PROMOTION:
            promoted_piece = move.promotion_piece if move.promotion_piece else 'Q'
            self.board[move.end_row][move.end_col] = move.piece_moved[0] + promoted_piece

        # Establish en-passant target square on double pawn moves
        if move.piece_moved[1] == 'P' and abs(move.start_row - move.end_row) == 2:
            self.enpassant_possible = ((move.start_row + move.end_row) // 2, move.end_col)
        else:
            self.enpassant_possible = None

        if move.move_type == Move.EN_PASSANT:
            self.board[move.start_row][move.end_col] = '--'

        # Reposition the rook during a castle move
        if move.move_type == Move.CASTLE:
            if move.end_col - move.start_col == 2:  # King side
                self.board[move.end_row][move.end_col - 1] = self.board[move.end_row][move.end_col + 1]
                self.board[move.end_row][move.end_col + 1] = '--'
            else:  # Queen side
                self.board[move.end_row][move.end_col + 1] = self.board[move.end_row][move.end_col - 2]
                self.board[move.end_row][move.end_col - 2] = '--'

        # Keep active piece sets updated
        _is_white_moved = not self.white_to_move
        friendly_pieces = self.white_pieces if _is_white_moved else self.black_pieces
        enemy_pieces = self.black_pieces if _is_white_moved else self.white_pieces

        friendly_pieces.remove((move.start_row, move.start_col))
        friendly_pieces.add((move.end_row, move.end_col))

        if move.piece_captured != '--':
            if move.move_type == Move.EN_PASSANT:
                enemy_pieces.remove((move.start_row, move.end_col))
            else:
                enemy_pieces.remove((move.end_row, move.end_col))

        if move.move_type == Move.CASTLE:
            if move.end_col - move.start_col == 2:  # King side
                friendly_pieces.remove((move.end_row, move.end_col + 1))
                friendly_pieces.add((move.end_row, move.end_col - 1))
            else:  # Queen side
                friendly_pieces.remove((move.end_row, move.end_col - 2))
                friendly_pieces.add((move.end_row, move.end_col + 1))

        if annotate:
            in_check, _, _ = self._check_pins_checks()
            move.is_check = in_check

        # Log state for threefold repetition tracking
        current_state = self.get_board_state()
        self.state_log.append(current_state)
        self.state_counts[current_state] = self.state_counts.get(current_state, 0) + 1

    def unmake_move(self) -> None:
        """
        Undo the last move made in the game.

        Restores the board, turn, castling rights, and internal tracking sets
        to their exact state before the previous move was executed.
        """
        if not self.move_log:
            return

        # Revert layout frequency and clocks
        current_state = self.state_log.pop()
        self.state_counts[current_state] -= 1
        if self.state_counts[current_state] == 0:
            del self.state_counts[current_state]

        self.halfmove_clock = self.halfmove_clock_log.pop()

        last_move = self.move_log.pop()
        self.board[last_move.start_row][last_move.start_col] = last_move.piece_moved
        self.board[last_move.end_row][last_move.end_col] = last_move.piece_captured
        self.white_to_move = not self.white_to_move

        if last_move.piece_moved == 'wK':
            self.white_king_location = (last_move.start_row, last_move.start_col)
        elif last_move.piece_moved == 'bK':
            self.black_king_location = (last_move.start_row, last_move.start_col)

        if last_move.move_type == Move.EN_PASSANT:
            self.board[last_move.end_row][last_move.end_col] = '--'
            self.board[last_move.start_row][last_move.end_col] = last_move.piece_captured

        self.enpassant_possible = self.enpassant_possible_log.pop()

        # Restore previous castling rights
        self.castle_rights_log.pop()
        castle_rights = self.castle_rights_log[-1]
        self.white_castle_king_side = castle_rights.white_king_side
        self.white_castle_queen_side = castle_rights.white_queen_side
        self.black_castle_king_side = castle_rights.black_king_side
        self.black_castle_queen_side = castle_rights.black_queen_side

        # Return rook to origin if castled
        if last_move.move_type == Move.CASTLE:
            if last_move.end_col - last_move.start_col == 2:  # King side
                self.board[last_move.end_row][last_move.end_col + 1] = self.board[last_move.end_row][last_move.end_col - 1]
                self.board[last_move.end_row][last_move.end_col - 1] = '--'
            else:  # Queen side
                self.board[last_move.end_row][last_move.end_col - 2] = self.board[last_move.end_row][last_move.end_col + 1]
                self.board[last_move.end_row][last_move.end_col + 1] = '--'

        # Restore pieces' tracking sets
        friendly_pieces = self.white_pieces if self.white_to_move else self.black_pieces
        enemy_pieces = self.black_pieces if self.white_to_move else self.white_pieces

        friendly_pieces.remove((last_move.end_row, last_move.end_col))
        friendly_pieces.add((last_move.start_row, last_move.start_col))

        if last_move.piece_captured != '--':
            if last_move.move_type == Move.EN_PASSANT:
                enemy_pieces.add((last_move.start_row, last_move.end_col))
            else:
                enemy_pieces.add((last_move.end_row, last_move.end_col))

        if last_move.move_type == Move.CASTLE:
            if last_move.end_col - last_move.start_col == 2:  # King side
                friendly_pieces.remove((last_move.end_row, last_move.end_col - 1))
                friendly_pieces.add((last_move.end_row, last_move.end_col + 1))
            else:  # Queen side
                friendly_pieces.remove((last_move.end_row, last_move.end_col + 1))
                friendly_pieces.add((last_move.end_row, last_move.end_col - 2))

    def get_board_state(self) -> tuple:
        """
        Generate a unique, immutable representation of the current board state.

        Converts the 2D mutable board list into a nested tuple. Using tuples
        eliminates memory allocation overhead during the AI's deep tree search
        and functions reliably as a hashable dictionary key.
        """
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

    def make_ai_move(self, move_tuple: tuple[int, int, int, int, int]) -> tuple[str, tuple, tuple]:
        """
        Execute a lightweight move specifically optimized for AI search trees.

        Note: This does not update the `halfmove_clock` or `state_counts` logs
        for performance. Transposition tables should handle repetition tracking
        in external search algorithms.

        Parameters
        ----------
        move_tuple : tuple of int
            Format: (start_row, start_col, end_row, end_col, move_type)
            Types: 0=Normal, 1=Castle, 2=En Passant, 3=Promo(Q), 4=R, 5=B, 6=N

        Returns
        -------
        tuple
            An undo package structured as (captured_piece, old_enpassant, old_castle_rights_tuple).
        """
        start_row, start_col, end_row, end_col, move_type = move_tuple

        piece_moved = self.board[start_row][start_col]
        captured_piece = self.board[end_row][end_col]

        old_castle_rights = (
            self.white_castle_king_side, self.white_castle_queen_side,
            self.black_castle_king_side, self.black_castle_queen_side
        )
        old_enpassant = self.enpassant_possible

        self.board[start_row][start_col] = '--'
        self.board[end_row][end_col] = piece_moved

        if piece_moved == 'wK':
            self.white_king_location = (end_row, end_col)
            self.white_castle_king_side = False
            self.white_castle_queen_side = False
        elif piece_moved == 'bK':
            self.black_king_location = (end_row, end_col)
            self.black_castle_king_side = False
            self.black_castle_queen_side = False

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

        elif move_type >= 3:  # Promotions
            promo_map = {3: 'Q', 4: 'R', 5: 'B', 6: 'N'}
            self.board[end_row][end_col] = piece_moved[0] + promo_map[move_type]

        if piece_moved[1] == 'P' and abs(start_row - end_row) == 2:
            self.enpassant_possible = ((start_row + end_row) // 2, end_col)
        else:
            self.enpassant_possible = None

        if piece_moved[1] == 'R':
            if start_row == 7:
                if start_col == 0: self.white_castle_queen_side = False
                elif start_col == 7: self.white_castle_king_side = False
            elif start_row == 0:
                if start_col == 0: self.black_castle_queen_side = False
                elif start_col == 7: self.black_castle_king_side = False

        if captured_piece != '--' and captured_piece[1] == 'R':
            if end_row == 7:
                if end_col == 0: self.white_castle_queen_side = False
                elif end_col == 7: self.white_castle_king_side = False
            elif end_row == 0:
                if end_col == 0: self.black_castle_queen_side = False
                elif end_col == 7: self.black_castle_king_side = False

        self.white_to_move = not self.white_to_move
        return captured_piece, old_enpassant, old_castle_rights

    def unmake_ai_move(
            self,
            move_tuple: tuple[int, int, int, int, int],
            undo_package: tuple[str, tuple, tuple]
    ) -> None:
        """
        Reverse state changes made by make_ai_move directly using primitive data.
        """
        start_row, start_col, end_row, end_col, move_type = move_tuple
        captured_piece, old_enpassant, old_castle_rights = undo_package

        self.white_to_move = not self.white_to_move
        piece_moved = self.board[end_row][end_col]

        if move_type >= 3:  # Promotion
            piece_moved = piece_moved[0] + 'P'

        self.board[start_row][start_col] = piece_moved

        if move_type == 2:  # En Passant
            self.board[end_row][end_col] = '--'
            self.board[start_row][end_col] = captured_piece
        else:
            self.board[end_row][end_col] = captured_piece

        if move_type == 1:  # Castle
            if end_col - start_col == 2:
                self.board[end_row][end_col + 1] = self.board[end_row][end_col - 1]
                self.board[end_row][end_col - 1] = '--'
            else:
                self.board[end_row][end_col - 2] = self.board[end_row][end_col + 1]
                self.board[end_row][end_col + 1] = '--'

        if piece_moved == 'wK':
            self.white_king_location = (start_row, start_col)
        elif piece_moved == 'bK':
            self.black_king_location = (start_row, start_col)

        self.enpassant_possible = old_enpassant
        (self.white_castle_king_side, self.white_castle_queen_side,
         self.black_castle_king_side, self.black_castle_queen_side) = old_castle_rights

    def _get_all_possible_moves(self, for_ai: bool = False) -> list:
        """Scan active pieces and fetch logic bounds for pseudo-legal moves."""
        possible_moves: list[Move] = []
        active_pieces: set[tuple[int, int]] = self.white_pieces if self.white_to_move else self.black_pieces

        for row, col in active_pieces:
            piece = self.board[row][col][1]
            self.move_functions[piece](row, col, possible_moves, for_ai)
            if piece == 'K':
                self._get_castle_moves(row, col, possible_moves, for_ai)
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
            (in_check: bool, pins: dict[(row, col): (d_row, d_col)], checks: list[(row, col, d_row, d_col)])
        """
        pins = {}
        checks = []
        in_check = False
        row, col = self.white_king_location if self.white_to_move else self.black_king_location
        directions = (
            (-1, 0), (0, -1), (1, 0), (0, 1),
            (-1, -1), (-1, 1), (1, -1), (1, 1)
        )

        for i in range(len(directions)):
            d = directions[i]
            possible_pins: tuple = ()
            for j in range(1, 8):
                end_row = row + d[0] * j
                end_col = col + d[1] * j
                if self._is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]

                    # Ignore moving phantom King to prevent false blocks
                    if end_piece[0] == self.friendly_color and end_piece[1] != 'K':
                        if len(possible_pins) == 0:
                            possible_pins = (end_row, end_col, d[0], d[1])
                        else:
                            break
                    elif end_piece[0] == self.enemy_color:
                        enemy_piece_type = end_piece[1]

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
                            else:
                                pins[(possible_pins[0], possible_pins[1])] = (possible_pins[2], possible_pins[3])
                                break
                        else:
                            break
                else:
                    break

        # Check for Knight attacks (they bypass pins)
        knight_moves = (
            (-2, -1), (-2, 1), (-1, -2), (-1, 2),
            (1, -2), (1, 2), (2, -1), (2, 1)
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
        Optimized for generating legal king bounds quickly.
        """
        enemy_color = 'b' if self.white_to_move else 'w'
        friendly_color = 'w' if self.white_to_move else 'b'

        directions = (
            (-1, 0), (0, -1), (1, 0), (0, 1),
            (-1, -1), (-1, 1), (1, -1), (1, 1)
        )

        for i in range(len(directions)):
            d = directions[i]
            for j in range(1, 8):
                end_row = row + d[0] * j
                end_col = col + d[1] * j
                if self._is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]

                    if end_piece[0] == friendly_color and end_piece[1] != 'K':
                        break
                    elif end_piece[0] == enemy_color:
                        enemy_piece_type = end_piece[1]

                        if 0 <= i <= 3 and enemy_piece_type in ('R', 'Q'):
                            return True
                        elif 4 <= i <= 7 and enemy_piece_type in ('B', 'Q'):
                            return True
                        elif j == 1 and enemy_piece_type == 'P':
                            if enemy_color == 'b' and 4 <= i <= 5: return True
                            elif enemy_color == 'w' and 6 <= i <= 7: return True
                        elif j == 1 and enemy_piece_type == 'K':
                            return True
                        else:
                            break
                else:
                    break

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

    def _get_pawn_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal moves for a pawn at the specified location."""
        move_amount = -1 if self.white_to_move else 1
        start_row = 6 if self.white_to_move else 1
        back_row = 0 if self.white_to_move else 7
        _is_back_row = row + move_amount == back_row

        piece_pinned = False
        pin_direction: tuple[int, int] | tuple[()] = ()
        if (row, col) in self.pins:
            piece_pinned = True
            pin_direction = self.pins[(row, col)]

        def _add_move(end_row: int, end_col: int) -> None:
            if _is_back_row:
                if for_ai:
                    # Append 4 promotion tuple states: 3=Q, 4=R, 5=B, 6=N
                    for m_type in (3, 4, 5, 6):
                        possible_moves.append((row, col, end_row, end_col, m_type))
                else:
                    for piece in ['Q', 'R', 'B', 'N']:
                        possible_moves.append(
                            Move.promotion(
                                (row, col), (end_row, end_col),
                                self.board, promotion_piece=piece
                            )
                        )
            else:
                if for_ai:
                    possible_moves.append((row, col, end_row, end_col, 0))
                else:
                    possible_moves.append(
                        Move.normal((row, col), (end_row, end_col), self.board)
                    )

        if self.board[row + move_amount][col] == '--':
            if not piece_pinned or pin_direction == (-1, 0) or pin_direction == (1, 0):
                _add_move(row + move_amount, col)
                if row == start_row and self.board[row + 2 * move_amount][col] == '--':
                    if for_ai:
                        possible_moves.append((row, col, row + 2 * move_amount, col, 0))
                    else:
                        possible_moves.append(
                            Move.normal((row, col), (row + 2 * move_amount, col), self.board)
                        )

        for col_offset in [-1, 1]:
            new_col = col + col_offset
            if 0 <= new_col < 8:
                if not piece_pinned or pin_direction == (move_amount, col_offset):
                    end_piece = self.board[row + move_amount][new_col]
                    if end_piece[0] == self.enemy_color:
                        _add_move(row + move_amount, new_col)

                    if (row + move_amount, new_col) == self.enpassant_possible:
                        if for_ai:
                            possible_moves.append((row, col, row + move_amount, new_col, 2))
                        else:
                            possible_moves.append(
                                Move.en_passant((row, col), (row + move_amount, new_col), self.board)
                            )

    def _get_rook_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal moves for a rook at the specified location."""
        directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        self._get_sliding_moves(row, col, possible_moves, directions, for_ai)

    def _get_bishop_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal moves for a bishop at the specified location."""
        directions = ((-1, -1), (1, 1), (1, -1), (-1, 1))
        self._get_sliding_moves(row, col, possible_moves, directions, for_ai)

    def _get_queen_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal moves for a queen at the specified location."""
        self._get_rook_moves(row, col, possible_moves, for_ai)
        self._get_bishop_moves(row, col, possible_moves, for_ai)

    def _get_sliding_moves(
            self,
            row: int,
            col: int,
            possible_moves: list,
            directions: tuple[tuple[int, int], ...],
            for_ai: bool = False
    ) -> None:
        """Helper method to iterate ray directions for sliding pieces (Rook, Bishop, Queen)."""
        piece_pinned = False
        pin_direction: tuple[int, int] | tuple[()] = ()
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
                    if not piece_pinned or pin_direction == (d[0], d[1]) or pin_direction == (-d[0], -d[1]):
                        end_piece = self.board[end_row][end_col]
                        if end_piece == '--':
                            if for_ai:
                                possible_moves.append((row, col, end_row, end_col, 0))
                            else:
                                possible_moves.append(Move.normal((row, col), (end_row, end_col), self.board))
                        elif end_piece[0] == self.enemy_color:
                            if for_ai:
                                possible_moves.append((row, col, end_row, end_col, 0))
                            else:
                                possible_moves.append(Move.normal((row, col), (end_row, end_col), self.board))
                            break
                        else:
                            break
                else:
                    break

    def _get_knight_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal moves for a knight at the specified location."""
        moves = (
            (-2, -1), (-2, 1), (2, -1), (2, 1),
            (-1, -2), (1, -2), (-1, 2), (1, 2),
        )
        piece_pinned = (row, col) in self.pins

        for move in moves:
            end_row = row + move[0]
            end_col = col + move[1]
            if self._is_on_board(end_row, end_col):
                if not piece_pinned:
                    end_piece = self.board[end_row][end_col]
                    if end_piece == '--' or end_piece[0] == self.enemy_color:
                        if for_ai:
                            possible_moves.append((row, col, end_row, end_col, 0))
                        else:
                            possible_moves.append(Move.normal((row, col), (end_row, end_col), self.board))

    def _get_king_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Get all pseudo-legal normal moves for a king validating safe surrounding squares."""
        row_moves = (-1, -1, -1, 0, 0, 1, 1, 1)
        col_moves = (-1, 0, 1, -1, 1, -1, 0, 1)

        for i in range(8):
            end_row = row + row_moves[i]
            end_col = col + col_moves[i]
            if self._is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece == '--' or end_piece[0] == self.enemy_color:
                    if not self._is_square_attacked(end_row, end_col):
                        if for_ai:
                            possible_moves.append((row, col, end_row, end_col, 0))
                        else:
                            possible_moves.append(Move.normal((row, col), (end_row, end_col), self.board))

    def _get_castle_moves(self, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
        """Identify available castling moves bound by legal logic and piece configurations."""
        if self.white_to_move:
            if (
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_king_side
                and self.board[7][5] == '--'
                and self.board[7][6] == '--'
                and self.board[7][7] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 5), (7, 6)]):
                    if for_ai:
                        possible_moves.append((7, 4, 7, 6, 1))
                    else:
                        possible_moves.append(Move.castle((7, 4), (7, 6), self.board))
            if (
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_queen_side
                and self.board[7][1] == '--'
                and self.board[7][2] == '--'
                and self.board[7][3] == '--'
                and self.board[7][0] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 3), (7, 2)]):
                    if for_ai:
                        possible_moves.append((7, 4, 7, 2, 1))
                    else:
                        possible_moves.append(Move.castle((7, 4), (7, 2), self.board))
        else:
            if (
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_king_side
                and self.board[0][5] == '--'
                and self.board[0][6] == '--'
                and self.board[0][7] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 5), (0, 6)]):
                    if for_ai:
                        possible_moves.append((0, 4, 0, 6, 1))
                    else:
                        possible_moves.append(Move.castle((0, 4), (0, 6), self.board))
            if (
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_queen_side
                and self.board[0][1] == '--'
                and self.board[0][2] == '--'
                and self.board[0][3] == '--'
                and self.board[0][0] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 3), (0, 2)]):
                    if for_ai:
                        possible_moves.append((0, 4, 0, 2, 1))
                    else:
                        possible_moves.append(Move.castle((0, 4), (0, 2), self.board))

    def _squares_safe_for_castle(self, squares: list[tuple[int, int]]) -> bool:
        """Check if castling squares are free from enemy attacks."""
        for square in squares:
            if self._is_square_attacked(square[0], square[1]):
                return False
        return True

    def _update_castle_rights(self, move: 'Move', record: bool = True) -> None:
        """Update castling privileges after kings or rooks abandon initial squares."""
        if move.piece_moved == 'wK':
            self.white_castle_king_side = False
            self.white_castle_queen_side = False
        elif move.piece_moved == 'bK':
            self.black_castle_king_side = False
            self.black_castle_queen_side = False
        elif move.piece_moved == 'wR':
            if move.start_row == 7:
                if move.start_col == 0: self.white_castle_queen_side = False
                elif move.start_col == 7: self.white_castle_king_side = False
        elif move.piece_moved == 'bR':
            if move.start_row == 0:
                if move.start_col == 0: self.black_castle_queen_side = False
                elif move.start_col == 7: self.black_castle_king_side = False

        if move.piece_captured == 'wR':
            if move.end_row == 7:
                if move.end_col == 0: self.white_castle_queen_side = False
                elif move.end_col == 7: self.white_castle_king_side = False
        elif move.piece_captured == 'bR':
            if move.end_row == 0:
                if move.end_col == 0: self.black_castle_queen_side = False
                elif move.end_col == 7: self.black_castle_king_side = False

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
        """Check if a given set of coordinates resides within the 8x8 matrix constraint."""
        return 0 <= row < 8 and 0 <= col < 8