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
            'P': self.get_pawn_moves,
            'R': self.get_rook_moves,
            'B': self.get_bishop_moves,
            'N': self.get_knight_moves,
            'Q': self.get_queen_moves,
            'K': self.get_king_moves,
        }

        # Current Kings' location
        self.white_king_location = (7, 4)
        self.black_king_location = (0, 4)
        # Kings' home square
        self.WHITE_KING_HOME_SQUARE = (7, 4)
        self.BLACK_KING_HOME_SQUARE = (0, 4)

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

        # Variables tracking the 50-move rule status
        self.halfmove_clock = 0
        self.halfmove_clock_log = []

        # Variables tracking threefold repetition status
        self.state_counts = {}
        self.state_log = []

        # Hash and store the absolute initial state configuration
        initial_state = self.get_board_state_string()
        self.state_counts[initial_state] = 1
        self.state_log.append(initial_state)

    def get_board_state_string(self) -> str:
        """
        Generate a unique string hash of the current board state configuration.
        Captures piece arrangements, en-passant squares, castling rights, and current turn.
        """
        board_str = "".join(["".join(row) for row in self.board])
        ep_str = str(self.enpassant_possible)
        castle_str = f"{int(self.white_castle_king_side)}{int(self.white_castle_queen_side)}{int(self.black_castle_king_side)}{int(self.black_castle_queen_side)}"
        turn = 'w' if self.white_to_move else 'b'
        return f"{board_str}_{ep_str}_{castle_str}_{turn}"

    @staticmethod
    def is_on_board(row: int, col: int) -> bool:
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
        self.update_castle_rights(move, record=True)

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
            if move.end_col - move.start_col == 2: # King side
                self.board[move.end_row][move.end_col - 1] = self.board[move.end_row][move.end_col + 1]  # Move Rook
                self.board[move.end_row][move.end_col + 1] = '--'  # Empty space where Rook was
            else: # Queen side
                self.board[move.end_row][move.end_col + 1] = self.board[move.end_row][move.end_col - 2]  # Move Rook
                self.board[move.end_row][move.end_col - 2] = '--'  # Empty space where Rook was

        # Update pieces' tracked squares list
        is_white_moved = not self.white_to_move
        friendly_pieces = self.white_pieces if is_white_moved else self.black_pieces
        enemy_pieces = self.black_pieces if is_white_moved else self.white_pieces

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
                friendly_pieces.remove((move.end_row, move.end_col + 1))  # Delete Rook's old square
                friendly_pieces.add((move.end_row, move.end_col - 1))  # Add new square
            else:  # Queen side
                friendly_pieces.remove((move.end_row, move.end_col - 2))
                friendly_pieces.add((move.end_row, move.end_col + 1))

        # Check analysis is bypassed if evaluate/simulation is requested by AI
        if annotate:
            in_check, _, _ = self.check_pins_checks()
            move.is_check = in_check

        # Log and increment the current board layout frequency for repetition check
        current_state = self.get_board_state_string()
        self.state_log.append(current_state)
        self.state_counts[current_state] = self.state_counts.get(current_state, 0) + 1

    def unmake_move(self) -> None:
        """
        Undo the last move made in the game.

        Restores the board, turn, castling rights, and internal tracking lists
        to their exact state before the previous move was executed.
        """
        if len(self.move_log) != 0: # Ensure there is a move to unmake
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
                    self.board[last_move.end_row][last_move.end_col + 1] = (
                        self.board[last_move.end_row][last_move.end_col - 1]  # Move Rook
                    )
                    self.board[last_move.end_row][last_move.end_col - 1] = '--'  # Empty space where Rook was
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

    def make_ai_move(self, move: 'Move') -> None:
        """
        Dedicated move function for the AI engine (placeholder).

        Parameters
        ----------
        move : Move
            The designated AI move to execute.
        """
        pass

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
        self.in_check, self.pins, self.checks = self.check_pins_checks()
        king_row, king_col = (
            self.white_king_location if self.white_to_move else self.black_king_location
        )
        if self.in_check:
            if len(self.checks) == 1:  # Single check --> Block check, capture piece, or move King
                moves = self.get_all_possible_moves()
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
                    if moves[i].piece_moved[1] != 'K':  # Move doesn't move the King
                        if (moves[i].end_row, moves[i].end_col) not in valid_squares:  # Move doesn't block check/capture
                            del moves[i]
            else:  # Double check --> King is forced to move
                self.get_king_moves(king_row, king_col, moves)
        else:  # Not in check
            moves = self.get_all_possible_moves()

        # Determine Checkmate or Stalemate statuses
        if len(moves) == 0: # Neither Checkmate nor Stalemate
            if self.in_check:
                self.is_checkmate = True
            else:
                self.is_stalemate = True
        else:
            self.is_checkmate = False
            self.is_stalemate = False

            # Enforce 50-move standard clock limits and threefold matching checks
            current_state = self.get_board_state_string()
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
                            move.disambiguation = Move.COLS_TO_FILES[move.start_col]
                        else:
                            rows = [m.start_row for m in matching_moves]
                            if rows.count(move.start_row) == 1:
                                move.disambiguation = Move.ROWS_TO_RANKS[move.start_row]
                            else:
                                move.disambiguation = (
                                    Move.COLS_TO_FILES[move.start_col] + Move.ROWS_TO_RANKS[move.start_row]
                                )

        # Double-check en-passant moves for hidden horizontal pins
        # Example: bR(a5) --- wP(f5) - bP(g5) --- wK(h5).
        for i in range(len(moves) - 1, -1, -1):
            if moves[i].move_type == Move.EN_PASSANT:
                self.make_move(moves[i], annotate=False)
                self.white_to_move = not self.white_to_move  # Temporarily switch turn back to evaluate check
                in_check, _, _ = self.check_pins_checks()
                self.white_to_move = not self.white_to_move  # Revert turn switch
                self.unmake_move()
                if in_check:
                    del moves[i]
        return moves

    def get_all_possible_moves(self) -> list['Move']:
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
                self.get_castle_moves(row, col, possible_moves)
        return possible_moves

    def get_pawn_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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

    def get_rook_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_bishop_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_queen_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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
        self.get_rook_moves(row, col, possible_moves)
        self.get_bishop_moves(row, col, possible_moves)

    def get_sliding_moves(
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
                if self.is_on_board(end_row, end_col):
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

    def get_knight_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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
            if self.is_on_board(end_row, end_col):
                if not piece_pinned:
                    end_piece = self.board[end_row][end_col]
                    if end_piece == '--' or end_piece[0] == self.enemy_color:
                        possible_moves.append(
                            Move.normal((row, col), (end_row, end_col), self.board)
                        )

    def get_king_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
        """
        Get all pseudo-legal normal moves for a king at the specified location.

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
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece == '--' or end_piece[0] == self.enemy_color:
                    # Test move validity by temporarily simulating the step
                    if self.friendly_color == 'w':
                        self.white_king_location = (end_row, end_col)
                    else:
                        self.black_king_location = (end_row, end_col)

                    in_check, _, _ = self.check_pins_checks()
                    if not in_check:
                        possible_moves.append(
                            Move.normal((row, col), (end_row, end_col), self.board)
                        )

                    # Revert simulation
                    if self.friendly_color == 'w':
                        self.white_king_location = (row, col)
                    else:
                        self.black_king_location = (row, col)

    def get_castle_moves(self, row: int, col: int, possible_moves: list['Move']) -> None:
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
                if self._squares_safe_for_castle([(7, 4), (7, 5), (7, 6)], 'w'):
                    possible_moves.append(Move.castle((7, 4), (7, 6), self.board))
            if (  # White Queen side castling
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_queen_side
                and self.board[7][1] == '--'
                and self.board[7][2] == '--'
                and self.board[7][3] == '--'
                and self.board[7][0] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 3), (7, 2)], 'w'):
                    possible_moves.append(Move.castle((7, 4), (7, 2), self.board))
        else:
            if (  # Black King side castling
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_king_side
                and self.board[0][5] == '--'
                and self.board[0][6] == '--'
                and self.board[0][7] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 5), (0, 6)], 'b'):
                    possible_moves.append(Move.castle((0, 4), (0, 6), self.board))
            if (  # Black Queen side castling
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_queen_side
                and self.board[0][1] == '--'
                and self.board[0][2] == '--'
                and self.board[0][3] == '--'
                and self.board[0][0] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 3), (0, 2)], 'b'):
                    possible_moves.append(Move.castle((0, 4), (0, 2), self.board))

    def _squares_safe_for_castle(self, squares: list[tuple[int, int]], king_color: str) -> bool:
        """
        Check if castling squares are free from enemy attacks.

        Temporarily places the king on intermediate squares and checks for checks.

        Parameters
        ----------
        squares : list of tuple of int
            The list of coordinate tuples the king moves through.
        king_color : str
            The color character of the king ('w' or 'b').

        Returns
        -------
        bool
            True if all squares are safe, False otherwise.
        """
        original_king_location = self.white_king_location if king_color == 'w' else self.black_king_location
        for square in squares:
            if king_color == 'w':
                self.white_king_location = square
            else:
                self.black_king_location = square
            in_check, _, _ = self.check_pins_checks()
            if in_check:
                # Revert immediately upon failure
                if king_color == 'w':
                    self.white_king_location = original_king_location
                else:
                    self.black_king_location = original_king_location
                return False

        # Revert king back to starting square
        if king_color == 'w':
            self.white_king_location = original_king_location
        else:
            self.black_king_location = original_king_location
        return True

    def check_pins_checks(self) -> tuple[
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
                if self.is_on_board(end_row, end_col):
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
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece[0] == self.enemy_color and end_piece[1] == 'N':
                    in_check = True
                    checks.append((end_row, end_col, move[0], move[1]))
                    break

        return in_check, pins, checks

    def update_castle_rights(self, move: 'Move', record: bool = True) -> None:
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


'''
Move:
- Storing move information: sq_start, sq_end, move_type, promotion
- Each move_type has separated method, access by:
    move.NORMAL(...) | move.EN_PASSANT(...) | move.CASTLE(...) | move.PROMOTION(...)
- Handling chess notation
'''
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
        """Construct a standard move."""
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
        """Check if the move is a promotion."""
        return self.move_type == self.PROMOTION

    @property
    def is_enpassant_move(self) -> bool:
        """Check if the move is an en-passant capture."""
        return self.move_type == self.EN_PASSANT

    @property
    def is_castle_move(self) -> bool:
        """Check if the move is a castle."""
        return self.move_type == self.CASTLE

    def get_file_rank(self, row: int, col: int) -> str:
        """
        Convert matrix coordinates to standard board notations.

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

    def get_chess_notation(self) -> str:
        """
        Construct the algebraic chess notation string for the move.
        Uses standard English letters (N, B, R, Q, K) to avoid missing
        Unicode character issues (rendering as boxes) in pygame default fonts.

        Returns
        -------
        str
            The algebraic notation representing the move executed.
        """
        if self.is_castle_move:
            notation = 'O-O' if self.end_col > self.start_col else 'O-O-O'
        else:
            notation = ''
            # Use standard piece letters instead of unicode symbols
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
            notation += self.get_file_rank(self.end_row, self.end_col)

            if self.is_pawn_promotion:
                notation += '=' + self.promotion_piece

        # Append check or checkmate symbols
        if getattr(self, 'is_checkmate', False):
            notation += '#'
        elif getattr(self, 'is_check', False):
            notation += '+'

        return notation


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