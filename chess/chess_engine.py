"""
Chess engine core.

This module holds the board state, generates legal moves, applies and undoes
moves, and tracks special chess rules such as check, castling, en passant, and
promotion.

The main pieces are:
- `GameState`: owns the board, turn state, move log, castling rights, and
  en passant state.
- `Move`: describes one move and its type.
- `CastleRights`: stores castling availability for undo support.

The move generator works in two phases:
1. Generate pseudo-legal moves for each piece.
2. Filter them through check logic so only legal moves remain.

The pygame UI uses the generated legal moves directly. It matches clicked
start/end squares against the legal move list and then calls `make_move()`
with the matching `Move` object.

This structure also supports future AI/search code: use `get_valid_moves()`
to expand nodes, `make_move()` / `unmake_move()` to traverse the tree, and
`Move.move_type` to preserve special-move semantics during evaluation.
"""

'''
GameState:
- Storing all the information about the current state of the game.
- Determining the valid moves at the current state.
- Keeping the move log.
'''
class GameState:
    def __init__(self):
        # The board is a 8x8 2D list, each element of the list has 2 characters:
        # The 1st char indicates the color of the piece ('b' OR 'w')
        # The 2nd char indicates the type of the piece ('B', 'K', 'N', 'P', 'Q', and 'R')
        # '--' represents an empty square
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
        self.white_to_move = True # Turn flag
        self.move_functions = {
            'P': self.get_pawn_moves,
            'R': self.get_rook_moves,
            'B': self.get_bishop_moves,
            'N': self.get_knight_moves,
            'Q': self.get_queen_moves,
            'K': self.get_king_moves,
        }

        # Store current location of Kings
        self.white_king_location = (7, 4)
        self.black_king_location = (0, 4)
        # CONSTANTS store Kings' home square
        self.WHITE_KING_HOME_SQUARE = (7, 4)
        self.BLACK_KING_HOME_SQUARE = (0, 4)

        self.in_check = False
        self.is_checkmate = False
        self.is_stalemate = False
        self.checks = []
        self.pins = []

        # Each element stores a Move(start_sq, end_sq, board, move_type, promotion_piece) object
        self.move_log = []

        # Squares' coordinates where en-passant capture is possible
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

    @staticmethod
    def is_on_board(row, col):
        return 0 <= row < 8 and 0 <= col < 8

    @property
    def friendly_color(self):
        return 'w' if self.white_to_move else 'b'

    @property
    def enemy_color(self):
        return 'b' if self.white_to_move else 'w'

    def make_move(self, move):
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
        # Update castling rights and append it into castle_rights_log by record=True
        self.update_castle_rights(move, record=True)

        # Pawn promotion
        if move.move_type == Move.PROMOTION:
            promoted_piece = move.promotion_piece if move.promotion_piece else 'Q'
            self.board[move.end_row][move.end_col] = move.piece_moved[0] + promoted_piece

        # En-passant
        # If pawn moves twice => The next move can capture en-passant
        if move.piece_moved[1] == 'P' and abs(move.start_row - move.end_row) == 2:
            self.enpassant_possible = ((move.start_row + move.end_row) // 2, move.end_col)
        else:
            self.enpassant_possible = ()
        # If en passant move => Update the capture to the board
        if move.move_type == Move.EN_PASSANT:
            self.board[move.start_row][move.end_col] = '--'

        # Castle moves
        if move.move_type == Move.CASTLE:
            if move.end_col - move.start_col == 2: # King side
                self.board[move.end_row][move.end_col - 1] = self.board[move.end_row][move.end_col + 1] # Move Rook
                self.board[move.end_row][move.end_col + 1] = '--' # Empty space where Rook was
            else: # Queen side
                self.board[move.end_row][move.end_col + 1] = self.board[move.end_row][move.end_col - 2] # Move Rook
                self.board[move.end_row][move.end_col - 2] = '--' # Empty space where Rook was

    def unmake_move(self):
        if len(self.move_log) != 0: # There are move to unmake
            last_move = self.move_log.pop()
            self.board[last_move.start_row][last_move.start_col] = last_move.piece_moved
            self.board[last_move.end_row][last_move.end_col] = last_move.piece_captured
            self.white_to_move = not self.white_to_move

            # Update King stored location
            if last_move.piece_moved == 'wK':
                self.white_king_location = (last_move.start_row, last_move.start_col)
            elif last_move.piece_moved == 'bK':
                self.black_king_location = (last_move.start_row, last_move.start_col)

            # Unmake the en passant
            if last_move.move_type == Move.EN_PASSANT:
                # Remove the pawn that do the en passant
                self.board[last_move.end_row][last_move.end_col] = '--'
                # Place the captured pawn back
                self.board[last_move.start_row][last_move.end_col] = last_move.piece_captured
            # Restore previous en passant state
            self.enpassant_possible = self.enpassant_possible_log.pop()

            # Give back castle rights
            self.castle_rights_log.pop()
            castle_rights = self.castle_rights_log[-1]
            self.white_castle_king_side = castle_rights.white_king_side
            self.white_castle_queen_side = castle_rights.white_queen_side
            self.black_castle_king_side = castle_rights.black_king_side
            self.black_castle_queen_side = castle_rights.black_queen_side
            # Unmake castle moves
            if last_move.move_type == Move.CASTLE:
                if last_move.end_col - last_move.start_col == 2:  # King side
                    self.board[last_move.end_row][last_move.end_col + 1] = (
                        self.board[last_move.end_row][last_move.end_col - 1]  # Move Rook
                    )
                    self.board[last_move.end_row][last_move.end_col - 1] = '--'  # Empty space where Rook was
                else:  # Queen side
                    self.board[last_move.end_row][last_move.end_col - 2] = (
                        self.board[last_move.end_row][last_move.end_col + 1] # Move Rook
                    )
                    self.board[last_move.end_row][last_move.end_col + 1] = '--'  # Empty space where Rook was

    '''
    All possible moves after considering checks and pinned pieces
    '''
    def get_valid_moves(self):
        moves = []
        self.in_check, self.pins, self.checks = self.check_pins_checks()
        king_row, king_col = (
            self.white_king_location if self.white_to_move else self.black_king_location
        )
        if self.in_check:
            if len(self.checks) == 1: # Only 1 check => Block check, capture, or move King
                # Block the check --> Move a piece into one of the squares between the checking piece and the King
                moves = self.get_all_possible_moves()
                check = self.checks[0]
                check_row, check_col = check[0], check[1]
                piece_checking = self.board[check_row][check_col]
                valid_squares = []
                # If a Knight --> Capture the Knight or Move the King
                if piece_checking[1] == 'N':
                    valid_squares = [(check_row, check_col)]
                else:
                    for i in range(1, 8):
                        # check[2] and check[3] --> Check's direction
                        valid_square = (
                            king_row + check[2] * i,
                            king_col + check[3] * i,
                        )
                        valid_squares.append(valid_square)
                        if valid_square == (check_row, check_col): # Reach the checking piece
                            break
                # Remove any moves that don't block check, capture piece, or move King
                for i in range(len(moves) - 1, -1, -1):
                    if moves[i].piece_moved[1] != 'K': # Move doesn't move the King
                        if (moves[i].end_row, moves[i].end_col) not in valid_squares: # Move doesn't block check/capture
                            moves.remove(moves[i])
            else: # Double check => King has to move
                self.get_king_moves(king_row, king_col, moves)
        else: # Not in check
            moves = self.get_all_possible_moves()
        # Check for Checkmate and Stalemate before return valid moves
        if len(moves) == 0: # Neither Checkmate nor Stalemate
            if self.in_check:
                self.is_checkmate = True
            else:
                self.is_stalemate = True
        else:
            self.is_checkmate = False
            self.is_stalemate = False
        # Double check en passant moves for hidden horizontal pins
        # En-passant horizontal pins: bR(a5) --- wP(f5) - bP(g5) --- wK(h5).
        for i in range(len(moves) - 1, -1, -1):
            if moves[i].move_type == Move.EN_PASSANT:
                self.make_move(moves[i])
                self.white_to_move = not self.white_to_move  # Switch turn back to check friendly King
                in_check, _, _ = self.check_pins_checks()
                self.white_to_move = not self.white_to_move  # Revert turn switch
                self.unmake_move()
                if in_check:
                    moves.remove(moves[i])
        return moves

    '''
    All possible moves without considering checks and pinned pieces
    '''
    def get_all_possible_moves(self):
        possible_moves = []
        for row in range(len(self.board)):
            for col in range(len(self.board[row])):
                turn = self.board[row][col][0]
                if (turn == 'w' and self.white_to_move) or (
                    turn == 'b' and not self.white_to_move
                ):
                    piece = self.board[row][col][1]
                    self.move_functions[piece](row, col, possible_moves)
                    if piece == 'K':
                        self.get_castle_moves(row, col, possible_moves)
        return possible_moves

    '''
    Get all possible move of each piece at (row, col)
    and add these moves to the possible_moves list
    '''
    # Pawn
    def get_pawn_moves(self, row, col, possible_moves):
        move_amount = -1 if self.white_to_move else 1
        start_row = 6 if self.white_to_move else 1
        back_row = 0 if self.white_to_move else 7
        _is_back_row = row + move_amount == back_row
        # Pinned Check
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                self.pins.remove(self.pins[i]) # Prevent Duplicates
                break
        # Move up 1 square
        if self.board[row + move_amount][col] == '--':
            if not piece_pinned or pin_direction == (-1, 0) or pin_direction == (1, 0):
                if _is_back_row:
                    possible_moves.append(
                        Move.promotion((row, col), (row + move_amount, col), self.board)
                    )
                else:
                    possible_moves.append(
                        Move.normal((row, col), (row + move_amount, col), self.board)
                    )
                # Move up 2 squares from the starting position
                if row == start_row and self.board[row + 2 * move_amount][col] == '--':
                    possible_moves.append(
                        Move.normal((row, col), (row + 2 * move_amount, col), self.board)
                    )
        # Capture to the left and to the right
        for col_offset in [-1, 1]:
            new_col = col + col_offset
            if 0 <= new_col < 8:
                if not piece_pinned or pin_direction == (move_amount, col_offset):
                    end_piece = self.board[row + move_amount][new_col]
                    if end_piece[0] == self.enemy_color:
                        if _is_back_row:
                            possible_moves.append(
                                Move.promotion((row, col), (row + move_amount, new_col), self.board)
                            )
                        else:
                            possible_moves.append(
                                Move.normal((row, col), (row + move_amount, new_col), self.board)
                            )
                    if (row + move_amount, new_col) == self.enpassant_possible:
                        possible_moves.append(
                            Move.en_passant((row, col), (row + move_amount, new_col), self.board)
                        )

    def get_rook_moves(self, row, col, possible_moves):
        directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_bishop_moves(self, row, col, possible_moves):
        directions = ((-1, -1), (1, 1), (1, -1), (-1, 1))
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_queen_moves(self, row, col, possible_moves):
        self.get_rook_moves(row, col, possible_moves)
        self.get_bishop_moves(row, col, possible_moves)

    # Rook, Bishop, and Queen ==> self.get_sliding_moves()
    def get_sliding_moves(self, row, col, possible_moves, directions):
        # Pinned Check
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                if self.board[row][col][1] != 'Q':
                    self.pins.remove(self.pins[i])
                break
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
                        if end_piece == '--': # Empty square
                            possible_moves.append(
                                Move.normal((row, col), (end_row, end_col), self.board)
                            )
                        elif end_piece[0] == self.enemy_color: # Occupied square with opponent piece
                            possible_moves.append(
                                Move.normal((row, col), (end_row, end_col), self.board)
                            )
                            break
                        else: # Occupied square with friendly piece
                            break
                else: # Off board
                    break

    def get_knight_moves(self, row, col, possible_moves):
        moves = (
            (-2, -1), (-2, 1),
            (2, -1), (2, 1),
            (-1, -2), (1, -2),
            (-1, 2), (1, 2),
        )
        # Pinned Check
        piece_pinned = False
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                self.pins.remove(self.pins[i])
                break
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

    def get_king_moves(self, row, col, possible_moves):
        row_moves = (-1, -1, -1, 0, 0, 1, 1, 1)
        col_moves = (-1, 0, 1, -1, 1, -1, 0, 1)
        for i in range(8):
            end_row = row + row_moves[i]
            end_col = col + col_moves[i]
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece == '--' or end_piece[0] == self.enemy_color:
                    # Place the King on the end square and check for checks
                    if self.friendly_color == 'w':
                        self.white_king_location = (end_row, end_col)
                    else:
                        self.black_king_location = (end_row, end_col)
                    in_check, _, _ = self.check_pins_checks()
                    if not in_check:
                        possible_moves.append(
                            Move.normal((row, col), (end_row, end_col), self.board)
                        )
                    # Return the King to the original square
                    if self.friendly_color == 'w':
                        self.white_king_location = (row, col)
                    else:
                        self.black_king_location = (row, col)

    def get_castle_moves(self, row, col, possible_moves):
        if self.white_to_move:
            if (  # King side
                (row, col) == self.WHITE_KING_HOME_SQUARE
                and self.white_castle_king_side
                and self.board[7][5] == '--'
                and self.board[7][6] == '--'
                and self.board[7][7] == 'wR'
            ):
                if self._squares_safe_for_castle([(7, 4), (7, 5), (7, 6)], 'w'):
                    possible_moves.append(Move.castle((7, 4), (7, 6), self.board))
            if (  # Queen side
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
            if (  # King side
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_king_side
                and self.board[0][5] == '--'
                and self.board[0][6] == '--'
                and self.board[0][7] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 5), (0, 6)], 'b'):
                    possible_moves.append(Move.castle((0, 4), (0, 6), self.board))
            if (  # Queen side
                (row, col) == self.BLACK_KING_HOME_SQUARE
                and self.black_castle_queen_side
                and self.board[0][1] == '--'
                and self.board[0][2] == '--'
                and self.board[0][3] == '--'
                and self.board[0][0] == 'bR'
            ):
                if self._squares_safe_for_castle([(0, 4), (0, 3), (0, 2)], 'b'):
                    possible_moves.append(Move.castle((0, 4), (0, 2), self.board))

    # Place King on squares between it and Rook and check for checks.
    # If being checked, then move King back to org square and refuse castle
    # If not being checked, then still move the King back
    def _squares_safe_for_castle(self, squares, king_color):
        original_king_location = self.white_king_location if king_color == 'w' else self.black_king_location
        for square in squares:
            if king_color == 'w':
                self.white_king_location = square
            else:
                self.black_king_location = square
            in_check, _, _ = self.check_pins_checks()
            if in_check:
                if king_color == 'w':
                    self.white_king_location = original_king_location
                else:
                    self.black_king_location = original_king_location
                return False
        if king_color == 'w':
            self.white_king_location = original_king_location
        else:
            self.black_king_location = original_king_location
        return True

    # From the king, check outward for pins and checks; simultaneously, keep track on the pins
    def check_pins_checks(self):
        pins = []
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
        # Check for Rooks, Bishops, Pawns check
        for i in range(len(directions)):
            d = directions[i]
            possible_pins = () # Reset for each direction
            for j in range(1, 8):
                end_row = row + d[0] * j
                end_col = col + d[1] * j
                if self.is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]
                    '''
                    Using end_piece[1] != 'K' cause the algorithm for get King move is basically move the King
                    so it will leave a 'phantom' King, which can make the system mistakenly assume that 'phantom' King
                    a blocking piece
                    '''
                    if end_piece[0] == self.friendly_color and end_piece[1] != 'K':
                        if len(possible_pins) == 0: # 1st friendly piece could be pinned
                            possible_pins = (end_row, end_col, d[0], d[1])
                        else: # 2nd friendly piece => No pins or checks in this direction
                            break
                    elif end_piece[0] == self.enemy_color:
                        enemy_piece_type = end_piece[1]
                        '''
                        5 circumstances:
                        - Orthogonally away             from King is a Rook
                        - Diagonally away               from King is a Bishop
                        - Diagonally 1 square away      from King is a Pawn
                        - Any direction                 from King is a Queen
                        - Any direction 1 square away   from King is a King
                        '''
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
                            if len(possible_pins) == 0: # No piece's blocking --> Checkmate
                                in_check = True
                                checks.append((end_row, end_col, d[0], d[1]))
                                break
                            else: # Piece's blocking --> Pin
                                pins.append(possible_pins)
                                break
                        else: # end_piece not applying checkmate
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

    def update_castle_rights(self, move, record=True):
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
        # Capture the Rook => Cannot castle
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
        if record: # Write to log, IF ONLY record = True
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

    def __init__(self, start_sq, end_sq, board, move_type='normal', promotion_piece='Q'):
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

    @classmethod
    def normal(cls, start_sq, end_sq, board):
        return cls(start_sq, end_sq, board, move_type=cls.NORMAL)

    @classmethod
    def en_passant(cls, start_sq, end_sq, board):
        return cls(start_sq, end_sq, board, move_type=cls.EN_PASSANT)

    @classmethod
    def castle(cls, start_sq, end_sq, board):
        return cls(start_sq, end_sq, board, move_type=cls.CASTLE)

    @classmethod
    def promotion(cls, start_sq, end_sq, board, promotion_piece='Q'):
        return cls(
            start_sq,
            end_sq,
            board,
            move_type=cls.PROMOTION,
            promotion_piece=promotion_piece,
        )

    '''
    Compare move information with other sources
    Current main purpose: standardizing the move information receive from chess_main.py
    '''
    def __eq__(self, other):
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
    def is_pawn_promotion(self):
        return self.move_type == self.PROMOTION

    @property
    def is_enpassant_move(self):
        return self.move_type == self.EN_PASSANT

    @property
    def is_castle_move(self):
        return self.move_type == self.CASTLE

    # Translate coordinates to chess notation
    def get_file_rank(self, row, col):
        return self.COLS_TO_FILES[col] + self.ROWS_TO_RANKS[row]

    def get_chess_notation(self):
        if self.is_castle_move:
            return 'O-O' if self.end_col > self.start_col else 'O-O-O'

        if self.piece_moved and self.piece_moved[1] != 'P':
            notation = self.piece_moved[1]
        else: # Pawn capture: '[start_file]x[end_file][end_rank]'
            notation = self.COLS_TO_FILES[self.start_col] if self.piece_captured != '--' else ''

        # Capture a piece
        if self.piece_captured != '--':
            notation += 'x'

        # PLAN: Cover the checkmate with an '#' at the end of the notation

        # Standard chess notation:
        # 1.e4 e5 2.Qh5?! Nc6 3.Bc4 Nf6?? 4.Qxf7#
        notation += self.get_file_rank(self.end_row, self.end_col)
        if self.is_pawn_promotion:
            notation += '=' + self.promotion_piece
        return notation


'''
Storing castle rights
'''
class CastleRights:
    def __init__(self, white_king_side, white_queen_side, black_king_side, black_queen_side):
        self.white_king_side = white_king_side
        self.white_queen_side = white_queen_side
        self.black_king_side = black_king_side
        self.black_queen_side = black_queen_side