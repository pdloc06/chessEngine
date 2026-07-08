"""
Main driver: handling user input and displaying current GameState object.
"""

import pygame as pg
from chess import chess_engine

WIDTH = HEIGHT = 512
DIMENSION = 8 # Dimensions of a chess board are 8x8
SQ_SIZE = WIDTH // DIMENSION
MAX_FPS = 20
IMAGES = {} # Storing chess pieces' images
board_colors = [pg.Color('white'), pg.Color('grey')]

'''
Handling user input and updating the graphics
'''
def main():
    pg.init()

    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    gs = chess_engine.GameState()
    valid_moves = gs.get_valid_moves()
    move_made = False # Flag variable, preventing overrun of the gs.get_valid_moves() function
    move_unmake = False # Flag variable for reversing the animation for unmade a move
    move_to_unmake = None

    load_images() # Only load images once, before entering the while loop

    running = True
    game_over = False

    sq_selected = () # No square is selected initially. Tuple: (row, col)
    player_clicks = [] # Keep track of the player clicks. List of two tuples: [(row, col), (row1, col1)]

    while running:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False
            # Mouse handle
            elif e.type == pg.MOUSEBUTTONDOWN:
                if not game_over:
                    location = pg.mouse.get_pos() # (x, y) location of the mouse
                    col = location[0] // SQ_SIZE
                    row = location[1] // SQ_SIZE

                    if sq_selected == (row, col): # User clicked the same square twice
                        sq_selected = () # Deselect and clear player clicks
                        player_clicks = []
                    else:
                        sq_selected = (row, col)
                        print(sq_selected)
                        player_clicks.append(sq_selected) # Store the square player selected

                    if len(player_clicks) == 2:
                        for move in valid_moves:
                            if (
                                move.start_row == player_clicks[0][0]
                                and move.start_col == player_clicks[0][1]
                                and move.end_row == player_clicks[1][0]
                                and move.end_col == player_clicks[1][1]
                            ):
                                gs.make_move(move)
                                print(move.get_chess_notation())
                                move_made = True
                                sq_selected = () # Deselect and clear player clicks
                                player_clicks = []
                                break
                        if not move_made:
                            player_clicks = [sq_selected]
            # Key handle
            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_z and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)): # CMD/CTRL + Z to undo last move
                    if len(gs.move_log) != 0:
                        move_to_unmake = gs.move_log[-1] # Store move information for animate unmaking the move
                        gs.unmake_move()
                        move_made = True # Considering unmake_move() equals to make a (reverse) move
                        move_unmake = True # Reverse move flag
                if e.key == pg.K_r and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)): # CMD/CTRL + R to restart game
                    # Reset everything
                    gs = chess_engine.GameState()
                    valid_moves = gs.get_valid_moves()
                    move_made = False
                    move_unmake = False
                    move_to_unmake = None
                    sq_selected = ()
                    player_clicks = []

        if move_made: # Regenerate the valid moves after the move is made
            if move_unmake:
                animate_move(move_to_unmake, screen, gs.board, clock, move_unmake=move_unmake)
            else:
                animate_move(gs.move_log[-1], screen, gs.board, clock)
            valid_moves = gs.get_valid_moves()
            # Reset flags and temp variable for move
            move_made = False
            move_unmake = False
            move_to_unmake = []

        draw_game_state(screen, gs, valid_moves, sq_selected)

        if gs.is_checkmate:
            game_over = True
            if gs.white_to_move:
                black_wins = False
                winning_animation(screen, gs, black_wins)
            else:
                black_wins = True
                winning_animation(screen, gs, black_wins)
        elif gs.is_stalemate:
            game_over = True
            stalemate_animation(screen, gs)

        clock.tick(MAX_FPS)
        pg.display.flip()

'''
Responsible for all the graphics within a current game state
'''
def draw_game_state(screen, gs, valid_moves, sq_selected):
    draw_board(screen)
    highlight_last_move(screen, gs)
    highlight_current_square(screen, gs, valid_moves, sq_selected)
    draw_pieces(screen, gs.board)

'''
Highlight the current selected square with yellow color
Add a little gray circle in middle of each possible square to move
'''
def highlight_current_square(screen, gs, valid_moves, sq_selected):
    if sq_selected != ():
        row, col = sq_selected
        if gs.board[row][col][0] == gs.friendly_color: # sq_selected contains a piece that can be moved
            # Highlight selected square
            current_sq = pg.Surface((SQ_SIZE, SQ_SIZE))
            current_sq.set_alpha(100) # Transparency value: 0 --> 255 (max, solid)
            current_sq.fill(pg.Color('yellow'))
            screen.blit(current_sq, (col * SQ_SIZE, row * SQ_SIZE))
            # Gray circles for possible move
            for move in valid_moves:
                if move.start_row == row and move.start_col == col:
                    movable_indicator = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
                    movable_indicator.fill((0, 0, 0, 0)) # Fill with transparent color
                    x_center = SQ_SIZE // 2 # Of the movable_indicator surface
                    y_center = SQ_SIZE // 2
                    radius = SQ_SIZE // 6
                    transparent_green = (100, 180, 120, 175) # (R, B, G, Alpha)
                    pg.draw.circle(movable_indicator, transparent_green, (x_center, y_center), radius)
                    screen.blit(movable_indicator, (move.end_col * SQ_SIZE, move.end_row * SQ_SIZE))

'''
Highlight last move on the board
'''
def highlight_last_move(screen, gs):
    if len(gs.move_log) > 0: # Check if there are any moves in the log
        last_move = gs.move_log[-1]  # Get the last move
        # Create a yellow surface with transparency
        highlight_sq = pg.Surface((SQ_SIZE, SQ_SIZE))
        highlight_sq.set_alpha(100)  # Transparency (0-255)
        highlight_sq.fill(pg.Color('yellow'))
        # Highlight the starting square of the move (start_row, start_col)
        start_x = last_move.start_col * SQ_SIZE
        start_y = last_move.start_row * SQ_SIZE
        screen.blit(highlight_sq, (start_x, start_y))
        # Highlight the ending square of the move (end_row, end_col)
        end_x = last_move.end_col * SQ_SIZE
        end_y = last_move.end_row * SQ_SIZE
        screen.blit(highlight_sq, (end_x, end_y))

'''
Draw the squares on the board
'''
def draw_board(screen):
    global board_colors
    for row in range(DIMENSION):
        for col in range(DIMENSION):
            color = board_colors[((row + col) % 2)]
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

'''
Initialize a global dictionary of images
'''
# PLAN: Add switch pieces' type feature
def load_images(pieces_type='standard'):
    pieces = ['bB', 'bK', 'bN', 'bP', 'bQ', 'bR', 'wB', 'wK', 'wN', 'wP', 'wQ', 'wR']
    for piece in pieces:
        IMAGES[piece] = pg.transform.smoothscale(
            pg.image.load('pieces/' + pieces_type + '/' + piece + '.png'),
            (SQ_SIZE, SQ_SIZE),
        )

'''
Animate a move
'''
def animate_move(move, screen, board, clock, move_unmake=False):
    global board_colors
    # Locate the start square and end square based on boolean variable move_unmake
    if move_unmake:
        anim_start_row, anim_start_col = move.end_row, move.end_col
        anim_end_row, anim_end_col = move.start_row, move.start_col
        erase_row, erase_col = move.start_row, move.start_col
    else:
        anim_start_row, anim_start_col = move.start_row, move.start_col
        anim_end_row, anim_end_col = move.end_row, move.end_col
        erase_row, erase_col = move.end_row, move.end_col
    # Calculate distance
    row_distance = anim_end_row - anim_start_row
    col_distance = anim_end_col - anim_start_col

    frames_per_square = 5 # PLAN: Add feature to adjust animation speed
    frame_count = (abs(row_distance) + abs(col_distance)) * frames_per_square

    for frame in range(frame_count + 1):
        row = anim_start_row + row_distance * frame / frame_count
        col = anim_start_col + col_distance * frame / frame_count
        draw_board(screen)
        draw_pieces(screen, board)
        # Erase the piece moved from its ending square
        color = board_colors[(erase_row + erase_col) % 2]
        erase_square = pg.Rect(erase_col * SQ_SIZE, erase_row * SQ_SIZE, SQ_SIZE, SQ_SIZE)
        pg.draw.rect(screen, color, erase_square)
        # Only redraw the captured piece if this is a normal move (not an undo)
        # Because if it's an undo, the draw_pieces function above has already redrawn the captured piece in its correct position
        if not move_unmake and move.piece_captured != '--':
            screen.blit(IMAGES[move.piece_captured], erase_square)
        # Draw the piece being moved
        screen.blit(IMAGES[move.piece_moved], pg.Rect(col * SQ_SIZE, row * SQ_SIZE, SQ_SIZE, SQ_SIZE))
        pg.display.flip()
        clock.tick(60)

'''
Animation for checkmate
'''
def winning_animation(screen, gs, black_wins):
    # Store 2 Kings' location based on boolean varial black_wins
    win_king_location = gs.white_king_location if black_wins else gs.black_king_location
    lose_king_location = gs.black_king_location if black_wins else gs.white_king_location
    # Draw red overlay for the losing King
    red_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    red_surface.fill((255, 0, 0, 150))  # Red with 150 transparency
    screen.blit(red_surface, (lose_king_location[1] * SQ_SIZE, lose_king_location[0] * SQ_SIZE))
    # Draw green overlay for the winning King
    green_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    green_surface.fill((100, 200, 100, 150))  # Green with transparency 150
    screen.blit(green_surface, (win_king_location[1] * SQ_SIZE, win_king_location[0] * SQ_SIZE))
    # Draw 'Winner' badge
    win_x = win_king_location[1] * SQ_SIZE + SQ_SIZE # The badge is put in the top right corner of the square
    win_y = win_king_location[0] * SQ_SIZE
    draw_badge(screen, "Winner", pg.Color('white'), pg.Color('green'), win_x, win_y)
    # Draw 'Checkmate' badge
    lose_x = lose_king_location[1] * SQ_SIZE + SQ_SIZE
    lose_y = lose_king_location[0] * SQ_SIZE
    draw_badge(screen, "Checkmate", pg.Color('red'), pg.Color('white'), lose_x, lose_y)

'''
Animation for stalemate
'''
def stalemate_animation(screen, gs):
    # Create a gray overlay
    gray_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    gray_surface.fill((150, 150, 150, 150))  # Gray with transparency 150
    # Apply overlay for 2 Kings
    for king_location in [gs.white_king_location, gs.black_king_location]:
        # Add gray color
        screen.blit(gray_surface, (king_location[1] * SQ_SIZE, king_location[0] * SQ_SIZE))
        # Draw 'Draw' badge
        badge_x = king_location[1] * SQ_SIZE + SQ_SIZE
        badge_y = king_location[0] * SQ_SIZE
        draw_badge(screen, "Draw", pg.Color('white'), pg.Color('black'), badge_x, badge_y)

'''
Helper function for draw the badge
'''
def draw_badge(screen, text, bg_color, text_color, center_x, center_y):
    # Set up the font
    font = pg.font.SysFont('Helvetica', 14, bold=True)
    text_surface = font.render(text, True, text_color)
    text_rect = text_surface.get_rect()
    # Background size of the badge
    padding_x, padding_y = 12, 6
    badge_rect = pg.Rect(0, 0, text_rect.width + padding_x, text_rect.height + padding_y)
    badge_rect.center = (center_x, center_y)
    # Clamp the badge rectangle to the screen, make sure it always in the screen
    badge_rect.clamp_ip(screen.get_rect())
    # Rounded the background
    pg.draw.rect(screen, bg_color, badge_rect, border_radius=10)
    # Add text into the badge
    text_rect.center = badge_rect.center
    screen.blit(text_surface, text_rect)

if __name__ == '__main__':
    main()