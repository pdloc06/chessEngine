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

        self.white_to_move = True

        self.move_log = []