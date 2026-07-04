"""
This is the main driver file responsible for handling user input and displaying current GameState object.
"""

import pygame as pg
from Chess import chessEngine

WIDTH = HEIGHT = 512
DIMENSION = 8 # Dimensions of a chess board are 8x8
SQ_SIZE = WIDTH // DIMENSION
MAX_FPS = 20
IMAGES = {}

'''
Initialize a global dictionary of images
'''
def load_images():
    pieces = ['bB', 'bK', 'bN', 'bP', 'bQ', 'bR', 'wB', 'wK', 'wN', 'wP', 'wQ', 'wR']
    for piece in pieces:
        IMAGES[piece] = pg.transform.smoothscale(pg.image.load('images/' + piece + '.png'), (SQ_SIZE, SQ_SIZE))

'''
Main driver: handling user input and updating the graphics
'''
def main():
    pg.init()

    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    gs = chessEngine.GameState()
    valid_moves = gs.get_valid_moves()
    move_made = False # Flag variable, preventing overrun of the gs.get_valid_moves() function

    load_images() # Only load images once, before entering the while loop

    running = True

    sq_selected = () # No square is selected initially. Tuple: (row, col)
    player_clicks = [] # Keep track of the player clicks. List of two tuples: [(row, col), (row1, col1)]

    while running:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False
            # Mouse handle
            elif e.type == pg.MOUSEBUTTONDOWN:
                location = pg.mouse.get_pos() # (x, y) location of the mouse

                col = location[0] // SQ_SIZE
                row = location[1] // SQ_SIZE

                if sq_selected == (row, col): # User clicked the same square twice
                    # Deselect and clear player clicks
                    sq_selected = ()
                    player_clicks = []
                else:
                    sq_selected = (row, col)
                    player_clicks.append(sq_selected) # Store the square player selected

                if len(player_clicks) == 2:
                    move = chessEngine.Move(player_clicks[0], player_clicks[1], gs.board)
                    if move in valid_moves: # Only allow to make a move when it's valid
                        gs.make_move(move)
                        print(move.get_chess_notation())
                        move_made = True
                    # Deselect and clear player clicks
                    sq_selected = ()
                    player_clicks = []
            # Key handle
            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_z and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)): # CMD/CTRL + Z to undo last move
                    gs.unmake_move()
                    move_made = True # Considering unmake_move() equals to make a (reverse) move

        if move_made: # Regenerate the valid moves after the move is made
            valid_moves = gs.get_valid_moves()
            move_made = False

        draw_game_state(screen, gs)

        clock.tick(MAX_FPS)
        pg.display.flip()

'''
Responsible for all the graphics within a current game state
'''
def draw_game_state(screen, gs):
    draw_board(screen)
    # PLANNING: add pieces highlighting and move suggestions
    draw_pieces(screen, gs.board)

'''
Draw the squares on the board
'''
def draw_board(screen):
    colors = [pg.Color('white'), pg.Color('grey')]
    for row in range(DIMENSION):
        for col in range(DIMENSION):
            color = colors[((row + col) % 2)]
            pg.draw.rect(screen, color, pg.Rect(col * SQ_SIZE, row * SQ_SIZE, SQ_SIZE, SQ_SIZE))

'''
Draw the pieces on the board using the current GameState.board
'''
def draw_pieces(screen, board):
    for row in range(DIMENSION):
        for col in range(DIMENSION):
            piece = board[row][col]
            if piece != '--': # Not empty square
                screen.blit(IMAGES[piece], pg.Rect(col * SQ_SIZE, row * SQ_SIZE, SQ_SIZE, SQ_SIZE))


if __name__ == '__main__':
    main()