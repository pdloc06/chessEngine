"""
GameState is responsible for:
- Storing all the information about the current state of the game.
- Determining the valid moves at the current state.
- Keeping the move log.
"""

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
            ['wR', 'wN', 'wB', 'wQ', 'wK', 'wB', 'wN', 'wR']
        ]
        self.move_functions = {
            'P': self.get_pawn_moves, 'R': self.get_rook_moves, 'B': self.get_bishop_moves,
            'N': self.get_knight_moves, 'Q': self.get_queen_moves, 'K': self.get_king_moves,
        }
        self.white_to_move = True
        self.move_log = []
        self.white_king_location = (7, 4)
        self.black_king_location = (0, 4)
        self.in_check = False
        self.pins = []
        self.checks = []

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
        self.board[move.start_row][move.start_col] = '--'
        self.board[move.end_row][move.end_col] = move.piece_moved
        self.move_log.append(move)
        self.white_to_move = not self.white_to_move
        # Update King stored location
        if move.piece_moved == 'wK':
            self.white_king_location = (move.end_row, move.end_col)
        elif move.piece_moved == 'bK':
            self.black_king_location = (move.end_row, move.end_col)

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

    '''
    All possible moves after considering checks and pinned pieces
    '''
    def get_valid_moves(self):
        moves = []
        self.in_check, self.pins, self.checks = self.check_pins_checks()
        king_row, king_col = self.white_king_location if self.white_to_move else self.black_king_location
        if self.in_check:
            if len(self.checks) == 1: # Only 1 check => Block check or Move King
                # Block the check --> Move a piece into one of the squares between the checking piece and the king
                moves = self.get_all_possible_moves()
                check = self.checks[0]
                check_row, check_col = check[0], check[1]
                piece_checking = self.board[check_row][check_col]
                valid_squares = []
                # If a Knight --> Capture the Knight or Move the King
                if piece_checking[1] == 'K':
                    valid_squares = [(check_row, check_col)]
                else:
                    for i in range(1, 8):
                        valid_square = (king_row + check[2] * i, king_col + check[3] * i) # check[2] and [3] is the direction
                        valid_squares.append(valid_square)
                        if valid_square == (check_row, check_col): # Once get to the piece_checking
                            break
                # Remove any moves that don't block check, capture piece, or move King
                for i in range(len(moves) - 1, -1, -1):
                    if moves[i].piece_moved[1] != 'K': # Move doesn't move the King
                        if (moves[i].end_row, moves[i].end_col) not in valid_squares: # Move doesn't block check or capture piece
                            moves.remove(moves[i])
            else: # Double check => King has to move
                self.get_king_moves(king_row, king_col, moves)
        else:
            moves = self.get_all_possible_moves()
        return moves




    '''
    All possible moves without considering checks and pinned pieces
    '''
    def get_all_possible_moves(self):
        possible_moves = []
        for row in range(len(self.board)):
            for col in range(len(self.board[row])):
                turn = self.board[row][col][0]
                if (turn == 'w' and self.white_to_move) or (turn == 'b' and not self.white_to_move):
                    piece = self.board[row][col][1]
                    self.move_functions[piece](row, col, possible_moves)
        return possible_moves

    '''
    Get all possible move of each piece at (row, col) and add these moves to the possible_moves list
    '''
    # Pawn
    def get_pawn_moves(self, row, col, possible_moves):
        move_amount = -1 if self.white_to_move else 1
        start_row = 6 if self.white_to_move else 1
        # Pinned Check
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                break
        # Move up 1 square
        if self.board[row + move_amount][col] == '--':
            if not piece_pinned or pin_direction == (-1, 0) or pin_direction == (1, 0):
                possible_moves.append(Move((row, col), (row + move_amount, col), self.board))
                # Move up 2 squares from the starting position
                if row == start_row and self.board[row + 2 * move_amount][col] == '--':
                    possible_moves.append(Move((row, col), (row + 2 * move_amount, col), self.board))
        # Capture left
        if col - 1 >= 0:
            end_piece = self.board[row + move_amount][col - 1]
            if end_piece[0] == self.enemy_color:
                if not piece_pinned or pin_direction == (move_amount, -1):
                    possible_moves.append(Move((row, col), (row + move_amount, col - 1), self.board))
        # Capture right
        if col + 1 < 8:
            end_piece = self.board[row + move_amount][col + 1]
            if end_piece[0] == self.enemy_color:
                if not piece_pinned or pin_direction == (move_amount, 1):
                    possible_moves.append(Move((row, col), (row + move_amount, col + 1), self.board))

        # for col_offset in [-1, 1]:
        #     new_col = col + col_offset
        #     if 0 <= new_col < 8:
        #         end_piece = self.board[row + move_amount][new_col]
        #         if end_piece != '--' and end_piece[0] != self.friendly_color:
        #             if not piece_pinned or pin_direction == (move_amount, col_offset):
        #                 possible_moves.append(Move((row, col), (row + move_amount, new_col), self.board))
        # PLAN: Pawn promotion, en passant

    # Rook, Bishop, and Queen ==> self.get_sliding_moves()
    def get_rook_moves(self, row, col, possible_moves):
        directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_bishop_moves(self, row, col, possible_moves):
        directions = ((-1, -1), (1, 1), (1, -1), (-1, 1))
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_queen_moves(self, row, col, possible_moves):
        self.get_rook_moves(row, col, possible_moves)
        self.get_bishop_moves(row, col, possible_moves)

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
        for direction in directions:
            end_row = row
            end_col = col
            while True:
                end_row += direction[0]
                end_col += direction[1]
                if self.is_on_board(end_row, end_col): # check if the square on the board
                    if (
                        not piece_pinned or
                        pin_direction == (direction[0], direction[1]) or
                        pin_direction == (-direction[0], -direction[1])
                    ):
                        end_piece = self.board[end_row][end_col]
                        if end_piece == '--': # Empty square
                            possible_moves.append(Move((row, col), (end_row, end_col), self.board))
                        elif self.friendly_color != end_piece[0]: # Compromised square with opponent piece
                            possible_moves.append(Move((row, col), (end_row, end_col), self.board))
                            break
                        else: # Compromised square with friendly piece
                            break
                else: # Off board
                    break

    # Knight
    def get_knight_moves(self, row, col, possible_moves):
        moves = ((-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (1, -2), (-1, 2), (1, 2))
        # Pinned Check
        piece_pinned = False
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                break
        for move in moves:
            end_row = row + move[0]
            end_col = col + move[1]
            if self.is_on_board(end_row, end_col):
                if not piece_pinned:
                    end_piece = self.board[end_row][end_col]
                    if end_piece[0] != self.friendly_color:
                        possible_moves.append(Move((row, col), (end_row, end_col), self.board))

    # King
    def get_king_moves(self, row, col, possible_moves):
        row_moves = (-1, -1, -1, 0, 0, 1, 1, 1)
        col_moves = (-1, 0, 1, -1, 1, -1, 0, 1)
        for i in range(8):
            end_row = row + row_moves[i]
            end_col = col + col_moves[i]
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece[0] != self.friendly_color: # Opponent piece
                    # Place the King on the end square and check for checks
                    if self.friendly_color == 'w':
                        self.white_king_location = (end_row, end_col)
                    else:
                        self.black_king_location = (end_row, end_col)
                    in_check, pins, checks = self.check_pins_checks() # Begin the check
                    if not in_check:
                        possible_moves.append(Move((row, col), (end_row, end_col), self.board))
                    # Return the King to the original square
                    if self.friendly_color == 'w':
                        self.white_king_location = (row, col)
                    else:
                        self.black_king_location = (row, col)

    def check_pins_checks(self):
        pins = []
        checks = []
        in_check = False
        if self.white_to_move:
            friendly_color, enemy_color = 'w', 'b'
            row, col = self.white_king_location
        else:
            friendly_color, enemy_color = 'b', 'w'
            row, col = self.black_king_location
        # From the king, check outward for pins and checks; simultaneously, keep track on the pins
        # Check for all pieces checks except Knights: Rooks, Bishops, Pawns
        directions = ((-1, 0), (0, -1), (1, 0), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
        for i in range(len(directions)):
            direction = directions[i]
            possible_pins = () # reset for each direction
            for j in range(1, 8):
                end_row = row + direction[0] * j
                end_col = col + direction[1] * j
                if self.is_on_board(end_row, end_col):
                    end_piece = self.board[end_row][end_col]
                    if end_piece[0] == friendly_color and end_piece[1] != 'K':
                        if len(possible_pins) == 0: # 1st friendly piece could be pinned
                            possible_pins = (end_row, end_col, direction[0], direction[1])
                        else: # 2nd friendly piece => No pins or checks in this direction
                            break
                    elif end_piece[0] == enemy_color:
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
                            (0 <= i <= 3 and enemy_piece_type == 'R') or
                            (4 <= i <= 7 and enemy_piece_type == 'B') or
                            (j == 1 and enemy_piece_type == 'P' and (
                                (enemy_color == 'b' and 4 <= i <= 5) or
                                (enemy_color == 'w' and 6 <= i <= 7)
                            )) or
                            (enemy_piece_type == 'Q') or
                            (j == 1 and enemy_piece_type == 'K')
                        ):
                            if len(possible_pins) == 0: # No piece's blocking ==> Checkmate
                                in_check = True
                                checks.append((end_row, end_col, direction[0], direction[1]))
                                break
                            else: # Piece's blocking ==> Pin
                                pins.append(possible_pins)
                                break
                        else: # end_piece not applying checkmate
                            break
                else: # Off board
                    break
        # Check for Knight checks
        knight_moves = ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1))
        for move in knight_moves:
            end_row = row + move[0]
            end_col = col + move[1]
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece[0] == enemy_color and end_piece[1] == 'N':
                    in_check = True
                    checks.append((end_row, end_col, move[0], move[1]))
                    break
        return in_check, pins, checks




class Move:
    # Dictionary to translate rows and cols to ranks and files of chess notation
    rows_to_ranks = {
        0: '8', 1: '7', 2: '6', 3: '5',
        4: '4', 5: '3', 6: '2', 7: '1',
    }
    cols_to_files = {
        0: 'a', 1: 'b', 2: 'c', 3: 'd',
        4: 'e', 5: 'f', 6: 'g', 7: 'h',
    }

    def __init__(self, start_sq, end_sq, board):
        self.start_row = start_sq[0]
        self.start_col = start_sq[1]
        self.end_row = end_sq[0]
        self.end_col = end_sq[1]

        self.piece_moved = board[self.start_row][self.start_col]
        self.piece_captured = board[self.end_row][self.end_col]
        self.move_ID = self.start_row * 1000 + self.start_col * 100 + self.end_row * 10 + self.end_col

    def __eq__(self, other):
        if isinstance(other, Move):
            return self.move_ID == other.move_ID
        return False

    def get_file_rank(self, row, col):
        return self.cols_to_files[col] + self.rows_to_ranks[row]

    def get_chess_notation(self):
        if self.piece_moved and self.piece_moved[1] != 'P':
            notation = self.piece_moved[1]
        else:
            notation = ''

        if self.piece_captured != '--':
            notation += 'x'

        # PLAN: Cover the checkmate with an '#' at the end of the notation

        # Standard chess notation:
        # 1.e4 e5 2.Qh5?! Nc6 3.Bc4 Nf6?? 4.Qxf7#
        return notation + self.get_file_rank(self.end_row, self.end_col)