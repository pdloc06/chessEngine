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
        IMAGES[piece] = pg.transform.scale(pg.image.load('images/' + piece + '.png'), (SQ_SIZE, SQ_SIZE))

'''
Main driver: handling user input and updating the graphics
'''
def main():
    pg.init()

    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    gs = chessEngine.GameState()

    load_images() # Only load images once, before entering the while loop

    running = True
    while running:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False

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