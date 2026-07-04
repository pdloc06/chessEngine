"""
This class is responsible for:
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

    @staticmethod
    def is_on_board(row, col):
        return 0 <= row < 8 and 0 <= col < 8

    @property
    def friendly_color(self):
        return 'w' if self.white_to_move else 'b'

    def make_move(self, move):
        self.board[move.start_row][move.start_col] = '--'
        self.board[move.end_row][move.end_col] = move.piece_moved
        self.move_log.append(move)
        self.white_to_move = not self.white_to_move

    def unmake_move(self):
        if len(self.move_log) != 0: # There are move to unmake
            last_move = self.move_log.pop()
            self.board[last_move.start_row][last_move.start_col] = last_move.piece_moved
            self.board[last_move.end_row][last_move.end_col] = last_move.piece_captured
            self.white_to_move = not self.white_to_move

    '''
    All possible moves after considering checkmates and pinned pieces
    '''
    def get_valid_moves(self):
        return self.get_all_possible_moves() # Temporary


    '''
    All possible moves without considering checkmates and pinned pieces
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
        # Move up 1 square
        if self.board[row + move_amount][col] == '--':
            possible_moves.append(Move((row, col), (row + move_amount, col), self.board))
            # Move up 2 squares from the starting position
            if row == start_row and self.board[row + 2 * move_amount][col] == '--':
                possible_moves.append(Move((row, col), (row + 2 * move_amount, col), self.board))
        # Capture left (white perspective)
        if col - 1 >= 0:
            end_piece = self.board[row + move_amount][col - 1]
            if end_piece != '--' and end_piece[0] != self.friendly_color:
                possible_moves.append(Move((row, col), (row + move_amount, col - 1), self.board))
        # Capture right (white perspective)
        if col + 1 < 8:
            end_piece = self.board[row + move_amount][col + 1]
            if end_piece != '--' and end_piece[0] != self.friendly_color:
                possible_moves.append(Move((row, col), (row + move_amount, col + 1), self.board))

    # Rook, Bishop, and Queen ==> get_sliding_moves()
    def get_rook_moves(self, row, col, possible_moves):
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_bishop_moves(self, row, col, possible_moves):
        directions = [(-1, -1), (1, 1), (1, -1), (-1, 1)]
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_queen_moves(self, row, col, possible_moves):
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (1, -1), (-1, 1)]
        self.get_sliding_moves(row, col, possible_moves, directions)

    def get_sliding_moves(self, row, col, possible_moves, directions):
        for d in directions:
            end_row = row
            end_col = col
            while True:
                end_row += d[0]
                end_col += d[1]
                if self.is_on_board(end_row, end_col): # check if the square on the board
                    end_piece = self.board[end_row][end_col]
                    if end_piece == '--': # Empty square
                        possible_moves.append(Move((row, col), (end_row, end_col), self.board))
                    elif self.friendly_color != end_piece[0]: # Compromised square with opponent piece
                        possible_moves.append(Move((row, col), (end_row, end_col), self.board))
                        break
                    else: # Compromised square with friendly piece
                        break
                else: # Out of the board
                    break

    # Knight and King ==> get_jump_moves()
    def get_knight_moves(self, row, col, possible_moves):
        directions = [(-2, -1), (-2, 1), (2, -1), (2, 1), (-1, -2), (1, -2), (-1, 2), (1, 2)]
        self.get_jump_moves(row, col, possible_moves, directions)

    # King
    def get_king_moves(self, row, col, possible_moves):
        directions = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        self.get_jump_moves(row, col, possible_moves, directions)

    def get_jump_moves(self, row, col, possible_moves, directions):
        for d in directions:
            end_row = row + d[0]
            end_col = col + d[1]
            if self.is_on_board(end_row, end_col):
                end_piece = self.board[end_row][end_col]
                if end_piece == '--' or self.friendly_color != end_piece[0]:
                    possible_moves.append(Move((row, col), (end_row, end_col), self.board))

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