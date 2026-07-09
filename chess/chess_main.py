"""
Main driver for the chess program.

This module handles user input (mouse clicks and keyboard events) and
displays the current GameState object using pygame. It manages the game
loop, graphics rendering, move animations, board flipping, and pawn promotion.
"""
import pygame as pg
from chess import chess_engine

BOARD_WIDTH = BOARD_HEIGHT = 512
MOVE_LOG_PANEL_WIDTH = 250
MOVE_LOG_PANEL_HEIGHT = BOARD_HEIGHT
WIDTH = BOARD_WIDTH + MOVE_LOG_PANEL_WIDTH
HEIGHT = BOARD_HEIGHT
DIMENSION = 8  # Dimensions of a chess board are 8x8
SQ_SIZE = BOARD_WIDTH // DIMENSION
MAX_FPS = 20
ANIMATION_FPS = 60
IMAGES = {}  # Storing chess pieces' images
COORD_SURFACES = {'white': {}, 'grey': {}}  # Storing coordinate surfaces
board_colors = [pg.Color('white'), pg.Color('grey')]

def main() -> None:
    """
    Initialize pygame, handle user input, and update graphics.

    This function sets up the game loop, captures mouse and keyboard events
    to execute moves, undo moves, reset the game, handle pawn promotions,
    and continuously redraws the updated game state including the flipped board view.
    """
    pg.init()

    screen = pg.display.set_mode((WIDTH, HEIGHT))
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    move_log_font = pg.font.SysFont('Arial', 16, False, False)
    coord_font = pg.font.SysFont('Arial', 12, bold=True)  # Font for board coordinates

    gs = chess_engine.GameState()
    valid_moves = gs.get_valid_moves()
    move_made = False
    move_unmake = False
    move_to_unmake = None

    undone_moves = []  # Stack to manage forward and backward history

    board_flipped = False  # State flag for flipping the board view
    promoting_move = None  # Stores the move object temporarily when a pawn promotes

    load_images()
    cache_coordinate_fonts(coord_font)

    running = True
    game_over = False

    sq_selected = None
    player_clicks = []

    while running:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False
            # Mouse handle
            elif e.type == pg.MOUSEBUTTONDOWN:
                if not game_over:
                    location = pg.mouse.get_pos()

                    # Intercept click if promotion interface is active
                    if promoting_move is not None:
                        menu_bg_rect, _, menu_rects = get_promotion_menu_rects(promoting_move, board_flipped)
                        clicked_option = None
                        for rect, option in menu_rects:
                            if rect.collidepoint(location):
                                clicked_option = option
                                break

                        # Apply the chosen promotion piece
                        if clicked_option in ['Q', 'N', 'R', 'B']:
                            promoting_move.promotion_piece = clicked_option
                            gs.make_move(promoting_move)
                            move_made = True
                            undone_moves.clear()

                        # Cancel the move if clicked 'x' or outside the menu
                        sq_selected = None
                        player_clicks = []
                        promoting_move = None
                        continue  # Skip to next event to avoid phantom clicks

                    # Logic when a player clicks inside the board dimensions
                    if location[0] < BOARD_WIDTH:
                        clicked_col = location[0] // SQ_SIZE
                        clicked_row = location[1] // SQ_SIZE

                        # Translate visual coordinates to logical board coordinates based on flip status
                        col = 7 - clicked_col if board_flipped else clicked_col
                        row = 7 - clicked_row if board_flipped else clicked_row

                        if sq_selected == (row, col):
                            sq_selected = None
                            player_clicks = []
                        else:
                            sq_selected = (row, col)
                            player_clicks.append(sq_selected)

                        if len(player_clicks) == 2:
                            for move in valid_moves:
                                if (
                                    move.start_row == player_clicks[0][0]
                                    and move.start_col == player_clicks[0][1]
                                    and move.end_row == player_clicks[1][0]
                                    and move.end_col == player_clicks[1][1]
                                ):
                                    if move.is_pawn_promotion:
                                        promoting_move = move  # Pause logic to display UI menu
                                    else:
                                        gs.make_move(move)
                                        move_made = True
                                        undone_moves.clear()
                                        sq_selected = None
                                        player_clicks = []
                                    break
                            if not move_made and promoting_move is None:
                                player_clicks = [sq_selected]

                    # Logic when a player clicks inside the move log panel
                    else:
                        prev_btn, next_btn, restart_btn, flip_btn = get_control_button_rects()

                        if restart_btn.collidepoint(location):
                            gs = chess_engine.GameState()
                            valid_moves = gs.get_valid_moves()
                            sq_selected = None
                            player_clicks = []
                            move_made = False
                            move_unmake = False
                            promoting_move = None
                            undone_moves.clear()
                        elif flip_btn.collidepoint(location):
                            board_flipped = not board_flipped
                        elif prev_btn.collidepoint(location):
                            promoting_move = None
                            if len(gs.move_log) > 0:
                                undone_moves.append(gs.move_log[-1])
                                gs.unmake_move()
                                move_made = True
                                move_unmake = True
                                move_to_unmake = undone_moves[-1]
                        elif next_btn.collidepoint(location):
                            promoting_move = None
                            if len(undone_moves) > 0:
                                move = undone_moves.pop()
                                gs.make_move(move)
                                move_made = True
                        else:
                            # Evaluate move selection clicks mathematically
                            promoting_move = None
                            y_offset = location[1] - 5
                            if 0 < y_offset < BOARD_HEIGHT - 100:
                                font_height = move_log_font.get_height()

                                # Synchronize item_height with draw_move_log spacing
                                item_height = font_height + 6

                                # Synchronize offset logic with the rendering function
                                max_lines = (BOARD_HEIGHT - 120) // item_height
                                total_lines = (len(gs.move_log) + 1) // 2
                                start_line = max(0, total_lines - max_lines)

                                # Calculate target index including the scrolled offset
                                clicked_line = y_offset // item_height
                                line_index = start_line + clicked_line
                                col_idx = 0 if location[0] < BOARD_WIDTH + 120 else 1
                                target_index = line_index * 2 + col_idx

                                full_len = len(gs.move_log) + len(undone_moves)
                                if target_index < full_len:
                                    target_len = target_index + 1
                                    current_len = len(gs.move_log)
                                    # Undo iteratively to reach history target
                                    if target_len < current_len:
                                        for _ in range(current_len - target_len):
                                            undone_moves.append(gs.move_log[-1])
                                            gs.unmake_move()
                                        move_made = True
                                        move_unmake = True
                                        move_to_unmake = undone_moves[-1]
                                    # Redo iteratively to reach history target
                                    elif target_len > current_len:
                                        for _ in range(target_len - current_len):
                                            gs.make_move(undone_moves.pop())
                                        move_made = True

            # Key handle
            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_z and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):
                    promoting_move = None
                    if len(gs.move_log) != 0:
                        move_to_unmake = gs.move_log[-1]
                        undone_moves.append(move_to_unmake)
                        gs.unmake_move()
                        move_made = True
                        move_unmake = True
                if e.key == pg.K_r and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):
                    gs = chess_engine.GameState()
                    valid_moves = gs.get_valid_moves()
                    move_made = False
                    move_unmake = False
                    move_to_unmake = None
                    sq_selected = None
                    player_clicks = []
                    promoting_move = None
                    undone_moves.clear()

        if move_made:
            if move_unmake:
                animate_move(move_to_unmake, screen, gs.board, clock, board_flipped, coord_font, move_unmake=True)
            else:
                animate_move(gs.move_log[-1], screen, gs.board, clock, board_flipped, coord_font)

            valid_moves = gs.get_valid_moves()

            # Identify absolute checkmate to format `#` in notation
            if gs.is_checkmate and len(gs.move_log) > 0:
                gs.move_log[-1].is_checkmate = True
                gs.move_log[-1].is_check = False

            move_made = False
            move_unmake = False
            move_to_unmake = None

        draw_game_state(screen, gs, valid_moves, sq_selected, board_flipped, coord_font)
        draw_move_log(screen, gs, move_log_font)

        # Draw promotion UI overlay if pawn reached end rank
        if promoting_move is not None:
            draw_promotion_menu(screen, promoting_move, board_flipped, move_log_font)

        if gs.is_checkmate:
            game_over = True
            if gs.white_to_move:
                winning_animation(screen, gs, False, board_flipped)
            else:
                winning_animation(screen, gs, True, board_flipped)
        elif gs.is_stalemate:
            game_over = True
            stalemate_animation(screen, gs, board_flipped)

        clock.tick(MAX_FPS)
        pg.display.flip()

def get_promotion_menu_rects(
        move: chess_engine.Move,
        board_flipped: bool
) -> tuple[pg.Rect, pg.Rect, list[tuple[pg.Rect, str]]]:
    """
    Calculate and return the bounding rectangles for the promotion interface.

    Parameters
    ----------
    move : chess_engine.Move
        The pawn promotion move object containing coordinates.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    tuple
        A tuple containing:
        - menu_bg_rect (pg.Rect): The bounding box for the entire menu.
        - shadow_rect (pg.Rect): The offset bounding box for the drop shadow.
        - rects (list of tuple): List mapping piece types/cancel option to their respective Rects.
    """
    end_draw_row = 7 - move.end_row if board_flipped else move.end_row
    draw_col = 7 - move.end_col if board_flipped else move.end_col

    # Direction dictates whether the menu spawns downwards (top of board) or upwards (bottom)
    direction = 1 if end_draw_row < 4 else -1

    rects = []
    pieces = ['Q', 'N', 'R', 'B']

    # Calculate piece bounding boxes
    for i, p in enumerate(pieces):
        r = end_draw_row + i * direction
        rect = pg.Rect(draw_col * SQ_SIZE, r * SQ_SIZE, SQ_SIZE, SQ_SIZE)
        rects.append((rect, p))

    # Calculate coordinates for the cancel 'x' button (Half size)
    x_height = SQ_SIZE // 2
    if direction == 1:
        x_y = (end_draw_row + 4) * SQ_SIZE
        menu_y = end_draw_row * SQ_SIZE
    else:
        # If spawning upwards, 'x' sits directly above the top piece
        x_y = (end_draw_row - 3) * SQ_SIZE - x_height
        menu_y = x_y

    x_rect = pg.Rect(draw_col * SQ_SIZE, x_y, SQ_SIZE, x_height)
    rects.append((x_rect, 'x'))

    # Background and shadow calculations
    menu_height = 4 * SQ_SIZE + x_height
    menu_bg_rect = pg.Rect(draw_col * SQ_SIZE, menu_y, SQ_SIZE, menu_height)

    shadow_offset = 6
    shadow_rect = pg.Rect(
        menu_bg_rect.x + shadow_offset,
        menu_bg_rect.y + shadow_offset,
        menu_bg_rect.width,
        menu_bg_rect.height
    )

    return menu_bg_rect, shadow_rect, rects

def draw_promotion_menu(screen: pg.Surface, move: chess_engine.Move, board_flipped: bool, font: pg.font.Font) -> None:
    """
    Draw the pawn promotion piece selection menu over the board.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    move : chess_engine.Move
        The move that triggered the pawn promotion.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    font : pygame.font.Font
        Font object used to render the 'x' cancel text.
    """
    color = 'w' if move.piece_moved[0] == 'w' else 'b'
    menu_bg_rect, shadow_rect, menu_rects = get_promotion_menu_rects(move, board_flipped)

    # 1. Draw Drop Shadow (Fake blur using semi-transparent black surface)
    shadow_surface = pg.Surface((shadow_rect.width, shadow_rect.height), pg.SRCALPHA)
    # Using pg.draw.rect to round the shadow (border_radius=5 sync with the background)
    pg.draw.rect(shadow_surface, (0, 0, 0, 80), shadow_surface.get_rect(), border_radius=5)
    screen.blit(shadow_surface, shadow_rect.topleft)

    # 2. Draw Menu Background (Matches the move log panel for consistency)
    bg_color = pg.Color('#3c3a38')
    pg.draw.rect(screen, bg_color, menu_bg_rect, border_radius=5)

    # Draw a thin subtle border around the entire menu for extra sharpness
    pg.draw.rect(screen, pg.Color('#5c5a58'), menu_bg_rect, 1, border_radius=5)

    # Calculate direction to draw the 'x' button separator line correctly
    end_draw_row = 7 - move.end_row if board_flipped else move.end_row
    direction = 1 if end_draw_row < 4 else -1

    # 3. Draw Pieces and Cancel Button
    for rect, option in menu_rects:
        if option == 'x':
            # Draw a subtle separator line above or below the 'x' button based on direction
            line_y = rect.top if direction == 1 else rect.bottom
            pg.draw.line(screen, pg.Color('#5c5a58'), (menu_bg_rect.left, line_y), (menu_bg_rect.right, line_y), 2)

            # Render 'x' character in the center of its half-height rect
            text = font.render('x', True, pg.Color('#989795'))
            screen.blit(text, text.get_rect(center=rect.center))
        else:
            # Draw the piece directly on the dark background (Borders removed)
            screen.blit(IMAGES[color + option], rect)

def draw_game_state(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    valid_moves: list[chess_engine.Move],
    sq_selected: tuple[int, int] | None,
    board_flipped: bool,
    coord_font: pg.font.Font
) -> None:
    """
    Render all graphics for the current game state.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    gs : GameState
        The current state of the game containing board information.
    valid_moves : list of Move
        The list of currently valid moves for highlighting.
    sq_selected : tuple of int or None
        The currently selected square coordinates as (row, col), or None if
        no square is currently selected.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    coord_font : pygame.font.Font
        Font object used to render the board's coordinate labels.
    """
    draw_board(screen, coord_font, board_flipped)
    highlight_last_move(screen, gs, board_flipped)
    highlight_current_square(screen, gs, valid_moves, sq_selected, board_flipped)
    draw_pieces(screen, gs.board, board_flipped)

def draw_board(screen: pg.Surface, coord_font: pg.font.Font, board_flipped: bool) -> None:
    """
    Draw the checkered squares and the coordinates on the board.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    coord_font : pygame.font.Font
        Font object used to render the board's coordinate labels.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    """
    global board_colors
    for row in range(DIMENSION):
        for col in range(DIMENSION):
            color_index = (row + col) % 2
            color = board_colors[color_index]

            draw_row = 7 - row if board_flipped else row
            draw_col = 7 - col if board_flipped else col
            rect = pg.Rect(draw_col * SQ_SIZE, draw_row * SQ_SIZE, SQ_SIZE, SQ_SIZE)
            pg.draw.rect(screen, color, rect)

            color_name = 'white' if color_index == 0 else 'grey'

            # Render Rank labels (1-8)
            if draw_col == 0:
                rank_text = str(8 - row)
                text_surf = COORD_SURFACES[color_name][rank_text]  # Lấy từ Cache
                screen.blit(text_surf, (rect.x + 2, rect.y + 2))

            # Render File labels (a-h)
            if draw_row == 7:
                file_text = chr(ord('a') + col)
                text_surf = COORD_SURFACES[color_name][file_text]
                # Position it near the bottom right corner of the square
                text_x = rect.x + SQ_SIZE - text_surf.get_width() - 2
                text_y = rect.y + SQ_SIZE - text_surf.get_height() - 2
                screen.blit(text_surf, (text_x, text_y))

def highlight_current_square(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    valid_moves: list[chess_engine.Move],
    sq_selected: tuple[int, int] | None,
    board_flipped: bool
) -> None:
    """
    Highlight the currently selected square and possible destinations.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    gs : GameState
        The current state of the game containing board information.
    valid_moves : list of Move
        The list of currently valid moves for highlighting.
    sq_selected : tuple of int or None
        The currently selected square coordinates as (row, col).
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    """
    if sq_selected is not None:
        row, col = sq_selected
        if gs.board[row][col][0] == gs.friendly_color:
            current_sq = pg.Surface((SQ_SIZE, SQ_SIZE))
            current_sq.set_alpha(100)
            current_sq.fill(pg.Color('yellow'))

            draw_row = 7 - row if board_flipped else row
            draw_col = 7 - col if board_flipped else col
            screen.blit(current_sq, (draw_col * SQ_SIZE, draw_row * SQ_SIZE))

            for move in valid_moves:
                if move.start_row == row and move.start_col == col:
                    movable_indicator = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
                    movable_indicator.fill((0, 0, 0, 0))
                    x_center, y_center = SQ_SIZE // 2, SQ_SIZE // 2
                    radius = SQ_SIZE // 6
                    transparent_green = (100, 180, 120, 175)
                    pg.draw.circle(movable_indicator, transparent_green, (x_center, y_center), radius)

                    m_draw_row = 7 - move.end_row if board_flipped else move.end_row
                    m_draw_col = 7 - move.end_col if board_flipped else move.end_col
                    screen.blit(movable_indicator, (m_draw_col * SQ_SIZE, m_draw_row * SQ_SIZE))

def highlight_last_move(screen: pg.Surface, gs: chess_engine.GameState, board_flipped: bool) -> None:
    """
    Highlight the starting and ending squares of the last executed move.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    gs : GameState
        The current state of the game containing the move log history.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    """
    if len(gs.move_log) > 0:
        last_move = gs.move_log[-1]
        highlight_sq = pg.Surface((SQ_SIZE, SQ_SIZE))
        highlight_sq.set_alpha(100)
        highlight_sq.fill(pg.Color('yellow'))

        start_r_draw = 7 - last_move.start_row if board_flipped else last_move.start_row
        start_c_draw = 7 - last_move.start_col if board_flipped else last_move.start_col
        screen.blit(highlight_sq, (start_c_draw * SQ_SIZE, start_r_draw * SQ_SIZE))

        end_r_draw = 7 - last_move.end_row if board_flipped else last_move.end_row
        end_c_draw = 7 - last_move.end_col if board_flipped else last_move.end_col
        screen.blit(highlight_sq, (end_c_draw * SQ_SIZE, end_r_draw * SQ_SIZE))

def draw_pieces(screen: pg.Surface, board: list[list[str]], board_flipped: bool) -> None:
    """
    Draw the chess pieces on the board according to the current game state.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    board : list of list of str
        The 2D board array populated with piece strings.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    """
    for row in range(DIMENSION):
        for col in range(DIMENSION):
            piece = board[row][col]
            if piece != '--':
                draw_row = 7 - row if board_flipped else row
                draw_col = 7 - col if board_flipped else col
                screen.blit(IMAGES[piece], pg.Rect(draw_col * SQ_SIZE, draw_row * SQ_SIZE, SQ_SIZE, SQ_SIZE))

def get_control_button_rects() -> tuple[pg.Rect, pg.Rect, pg.Rect, pg.Rect]:
    """
    Calculate and return the bounding rectangles for all UI control buttons.

    Returns
    -------
    tuple
        A tuple of four pygame.Rect objects: (prev_btn, next_btn, restart_btn, flip_btn).
    """
    prev_btn = pg.Rect(BOARD_WIDTH + 10, BOARD_HEIGHT - 80, (MOVE_LOG_PANEL_WIDTH - 30) // 2, 30)
    next_btn = pg.Rect(BOARD_WIDTH + 20 + prev_btn.width, BOARD_HEIGHT - 80, prev_btn.width, 30)
    restart_btn = pg.Rect(BOARD_WIDTH + 10, BOARD_HEIGHT - 40, MOVE_LOG_PANEL_WIDTH - 70, 30)
    flip_btn = pg.Rect(BOARD_WIDTH + MOVE_LOG_PANEL_WIDTH - 50, BOARD_HEIGHT - 40, 40, 30)
    return prev_btn, next_btn, restart_btn, flip_btn

def draw_move_log(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    font: pg.font.Font
) -> None:
    """
    Draw the move log interface containing notation history and system controls.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : GameState
        The current game state instance.
    font : pygame.font.Font
        The font object utilized for rendering the log text.
    """
    panel_rect = pg.Rect(BOARD_WIDTH, 0, MOVE_LOG_PANEL_WIDTH, MOVE_LOG_PANEL_HEIGHT)
    pg.draw.rect(screen, pg.Color('#262421'), panel_rect)

    move_texts = []
    for i in range(0, len(gs.move_log), 2):
        move_string = str(i // 2 + 1) + "."
        white_move = gs.move_log[i].get_chess_notation()
        black_move = gs.move_log[i + 1].get_chess_notation() if (i + 1) < len(gs.move_log) else ""
        move_texts.append((move_string, white_move, black_move))

    text_y = 5
    line_spacing = 6  # Increased spacing slightly to accommodate the selection box
    item_height = font.get_height() + line_spacing

    # Calculate maximum visible lines, subtracting 120 to leave safe space for buttons
    max_lines = (BOARD_HEIGHT - 120) // item_height
    start_line = max(0, len(move_texts) - max_lines)

    # Get the index of the currently active half-move
    current_move_index = len(gs.move_log) - 1

    # Only draw the lines that fit within the visible window
    for i in range(start_line, start_line + max_lines):
        if i >= len(move_texts):
            break

        num_str, w_move, b_move = move_texts[i]

        # 1. Draw alternating row background colors (Zebra striping)
        row_color = pg.Color('#2b2927') if i % 2 == 0 else pg.Color('#262421')
        row_rect = pg.Rect(BOARD_WIDTH, text_y, MOVE_LOG_PANEL_WIDTH, item_height)
        pg.draw.rect(screen, row_color, row_rect)

        # Define specific x-coordinates for alignment
        num_x = BOARD_WIDTH + 15
        w_x = BOARD_WIDTH + 65
        b_x = BOARD_WIDTH + 155

        # Vertical offset to center text perfectly within the row's height
        text_offset_y = text_y + (line_spacing // 2)

        num_surface = font.render(num_str, True, pg.Color('#989795'))
        screen.blit(num_surface, (num_x, text_offset_y))

        # 2. Draw White's move notation
        is_w_selected = (i * 2 == current_move_index)
        if is_w_selected:
            # Draw the selection box for the active move
            w_bg_rect = pg.Rect(w_x - 5, text_y, 80, item_height)
            pg.draw.rect(screen, pg.Color('#4c4a48'), w_bg_rect, border_radius=4)
            w_color = pg.Color('white')
        else:
            w_color = pg.Color('#c9c8c7')  # Dimmer text for inactive moves

        w_surface = font.render(w_move, True, w_color)
        screen.blit(w_surface, (w_x, text_offset_y))

        # 3. Draw Black's move notation
        if b_move:
            is_b_selected = (i * 2 + 1 == current_move_index)
            if is_b_selected:
                # Draw the selection box for the active move
                b_bg_rect = pg.Rect(b_x - 5, text_y, 80, item_height)
                pg.draw.rect(screen, pg.Color('#4c4a48'), b_bg_rect, border_radius=4)
                b_color = pg.Color('white')
            else:
                b_color = pg.Color('#c9c8c7')

            b_surface = font.render(b_move, True, b_color)
            screen.blit(b_surface, (b_x, text_offset_y))

        text_y += item_height

    # Rendering match end states (Checkmate/Stalemate)
    if gs.is_stalemate:
        end_surface = font.render("1/2-1/2", True, pg.Color('#989795'))
        screen.blit(end_surface, (BOARD_WIDTH + 15, text_y))
    elif gs.is_checkmate:
        end_text = "0-1" if gs.white_to_move else "1-0"
        end_surface = font.render(end_text, True, pg.Color('#989795'))
        screen.blit(end_surface, (BOARD_WIDTH + 15, text_y))

    # Control buttons layout
    btn_color = pg.Color('#3c3a38')
    text_color = pg.Color('white')

    prev_btn = pg.Rect(BOARD_WIDTH + 10, BOARD_HEIGHT - 80, (MOVE_LOG_PANEL_WIDTH - 30) // 2, 30)
    pg.draw.rect(screen, btn_color, prev_btn, border_radius=5)
    prev_text = font.render("<", True, text_color)
    screen.blit(prev_text, prev_text.get_rect(center=prev_btn.center))

    next_btn = pg.Rect(BOARD_WIDTH + 20 + prev_btn.width, BOARD_HEIGHT - 80, prev_btn.width, 30)
    pg.draw.rect(screen, btn_color, next_btn, border_radius=5)
    next_text = font.render(">", True, text_color)
    screen.blit(next_text, next_text.get_rect(center=next_btn.center))

    restart_btn = pg.Rect(BOARD_WIDTH + 10, BOARD_HEIGHT - 40, MOVE_LOG_PANEL_WIDTH - 70, 30)
    pg.draw.rect(screen, btn_color, restart_btn, border_radius=5)
    restart_text = font.render("Restart Game", True, text_color)
    screen.blit(restart_text, restart_text.get_rect(center=restart_btn.center))

    # Flip board perspective button
    flip_btn = pg.Rect(BOARD_WIDTH + MOVE_LOG_PANEL_WIDTH - 50, BOARD_HEIGHT - 40, 40, 30)
    pg.draw.rect(screen, btn_color, flip_btn, border_radius=5)
    flip_text = font.render("Flip", True, text_color)
    screen.blit(flip_text, flip_text.get_rect(center=flip_btn.center))

def load_images(pieces_type: str = 'standard') -> None:
    """
    Initialize a global dictionary of images and load piece assets.

    Parameters
    ----------
    pieces_type : str, optional
        The subdirectory name for the image set. Default is 'standard'.
    """
    pieces = ['bB', 'bK', 'bN', 'bP', 'bQ', 'bR', 'wB', 'wK', 'wN', 'wP', 'wQ', 'wR']
    for piece in pieces:
        IMAGES[piece] = pg.transform.smoothscale(
            pg.image.load('pieces/' + pieces_type + '/' + piece + '.png'),
            (SQ_SIZE, SQ_SIZE),
        )

def cache_coordinate_fonts(coord_font: pg.font.Font) -> None:
    """
    Pre-render coordinate labels (1-8 and a-h) into surfaces to optimize performance.

    This function caches the surfaces for both 'white' and 'gray' square text colors
    to avoid expensive font.render() calls during the game loop.

    Parameters
    ----------
    coord_font : pg.font.Font
        The font object used to render board coordinate labels.
    """
    global board_colors, COORD_SURFACES
    for color_name, bg_color in zip(['white', 'grey'], board_colors):
        text_color = board_colors[1] if color_name == 'white' else board_colors[0]
        # Ranks 1-8
        for rank in range(1, 9):
            COORD_SURFACES[color_name][str(rank)] = coord_font.render(str(rank), True, text_color)
        # Files a-h
        for file in range(8):
            file_char = chr(ord('a') + file)
            COORD_SURFACES[color_name][file_char] = coord_font.render(file_char, True, text_color)

def animate_move(
    move: chess_engine.Move,
    screen: pg.Surface,
    board: list[list[str]],
    clock: pg.time.Clock,
    board_flipped: bool,
    coord_font: pg.font.Font,
    move_unmake: bool = False
) -> None:
    """
    Animate a move sliding across the board.

    Parameters
    ----------
    move : chess_engine.Move
        The move entity encapsulating coordinate changes.
    screen : pygame.Surface
        The main display surface.
    board : list of list of str
        The 2D board array configuration.
    clock : pygame.time.Clock
        Clock object utilized for framerate regulation.
    board_flipped : bool
        Flag indicating if the board is rendering from black's perspective.
    coord_font : pygame.font.Font
        The font for rendering coordinate text beneath the animation.
    move_unmake : bool, optional
        Flag signifying if the animation is reversing a previous move. Default is False.
    """
    global board_colors
    if move_unmake:
        anim_start_row, anim_start_col = move.end_row, move.end_col
        anim_end_row, anim_end_col = move.start_row, move.start_col
        erase_row, erase_col = move.start_row, move.start_col
    else:
        anim_start_row, anim_start_col = move.start_row, move.start_col
        anim_end_row, anim_end_col = move.end_row, move.end_col
        erase_row, erase_col = move.end_row, move.end_col

    # Calculate actual visual positions on screen
    start_r_draw = 7 - anim_start_row if board_flipped else anim_start_row
    start_c_draw = 7 - anim_start_col if board_flipped else anim_start_col
    end_r_draw = 7 - anim_end_row if board_flipped else anim_end_row
    end_c_draw = 7 - anim_end_col if board_flipped else anim_end_col

    row_distance = end_r_draw - start_r_draw
    col_distance = end_c_draw - start_c_draw

    frames_per_square = 5
    frame_count = (abs(row_distance) + abs(col_distance)) * frames_per_square

    if frame_count == 0: return

    for frame in range(frame_count + 1):
        row = start_r_draw + row_distance * frame / frame_count
        col = start_c_draw + col_distance * frame / frame_count
        draw_board(screen, coord_font, board_flipped)
        draw_pieces(screen, board, board_flipped)

        erase_r_draw = 7 - erase_row if board_flipped else erase_row
        erase_c_draw = 7 - erase_col if board_flipped else erase_col

        color = board_colors[(erase_row + erase_col) % 2]
        erase_square = pg.Rect(erase_c_draw * SQ_SIZE, erase_r_draw * SQ_SIZE, SQ_SIZE, SQ_SIZE)
        pg.draw.rect(screen, color, erase_square)

        if not move_unmake and move.piece_captured != '--':
            if move.is_enpassant_move:
                ep_r_draw = 7 - move.start_row if board_flipped else move.start_row
                ep_c_draw = 7 - move.end_col if board_flipped else move.end_col
                enpassant_square = pg.Rect(ep_c_draw * SQ_SIZE, ep_r_draw * SQ_SIZE, SQ_SIZE, SQ_SIZE)
                screen.blit(IMAGES[move.piece_captured], enpassant_square)
            else:
                screen.blit(IMAGES[move.piece_captured], erase_square)

        screen.blit(IMAGES[move.piece_moved], pg.Rect(col * SQ_SIZE, row * SQ_SIZE, SQ_SIZE, SQ_SIZE))
        pg.display.flip()
        clock.tick(ANIMATION_FPS)

def winning_animation(screen: pg.Surface, gs: chess_engine.GameState, white_wins: bool, board_flipped: bool) -> None:
    """
    Render visual indicators and badges when the game ends in checkmate.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : GameState
        The game state containing king location parameters.
    white_wins : bool
        Flag signifying whether white executes the winning attack.
    board_flipped : bool
        Flag indicating if the board view is swapped.
    """
    win_king_location = gs.white_king_location if white_wins else gs.black_king_location
    lose_king_location = gs.black_king_location if white_wins else gs.white_king_location

    win_r_draw = 7 - win_king_location[0] if board_flipped else win_king_location[0]
    win_c_draw = 7 - win_king_location[1] if board_flipped else win_king_location[1]
    lose_r_draw = 7 - lose_king_location[0] if board_flipped else lose_king_location[0]
    lose_c_draw = 7 - lose_king_location[1] if board_flipped else lose_king_location[1]

    red_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    red_surface.fill((255, 0, 0, 150))
    screen.blit(red_surface, (lose_c_draw * SQ_SIZE, lose_r_draw * SQ_SIZE))

    green_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    green_surface.fill((100, 200, 100, 150))
    screen.blit(green_surface, (win_c_draw * SQ_SIZE, win_r_draw * SQ_SIZE))

    win_x = win_c_draw * SQ_SIZE + SQ_SIZE
    win_y = win_r_draw * SQ_SIZE
    draw_badge(screen, "Winner", pg.Color('white'), pg.Color('green'), win_x, win_y)

    lose_x = lose_c_draw * SQ_SIZE + SQ_SIZE
    lose_y = lose_r_draw * SQ_SIZE
    draw_badge(screen, "Checkmate", pg.Color('red'), pg.Color('white'), lose_x, lose_y)

def stalemate_animation(screen: pg.Surface, gs: chess_engine.GameState, board_flipped: bool) -> None:
    """
    Render visual indicators and badges when the game ends in a draw.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : GameState
        The game state tracking both kings.
    board_flipped : bool
        Flag dictating whether the board rendering is reversed.
    """
    gray_surface = pg.Surface((SQ_SIZE, SQ_SIZE), pg.SRCALPHA)
    gray_surface.fill((150, 150, 150, 150))

    for king_location in [gs.white_king_location, gs.black_king_location]:
        r_draw = 7 - king_location[0] if board_flipped else king_location[0]
        c_draw = 7 - king_location[1] if board_flipped else king_location[1]

        screen.blit(gray_surface, (c_draw * SQ_SIZE, r_draw * SQ_SIZE))
        badge_x = c_draw * SQ_SIZE + SQ_SIZE
        badge_y = r_draw * SQ_SIZE
        draw_badge(screen, "Draw", pg.Color('white'), pg.Color('black'), badge_x, badge_y)

def draw_badge(
    screen: pg.Surface,
    text: str,
    bg_color: pg.Color,
    text_color: pg.Color,
    center_x: int,
    center_y: int
) -> None:
    """
    Render a stylized text badge with rounded corners.

    Parameters
    ----------
    screen : pygame.Surface
        The main rendering context.
    text : str
        Text string to display within the badge boundaries.
    bg_color : pygame.Color
        Background color filling the badge rectangle.
    text_color : pygame.Color
        Foreground color applied to the text.
    center_x : int
        The intended horizontal center pixel coordinate.
    center_y : int
        The intended vertical center pixel coordinate.
    """
    font = pg.font.SysFont('Helvetica', 14, bold=True)
    text_surface = font.render(text, True, text_color)
    text_rect = text_surface.get_rect()

    padding_x, padding_y = 12, 6
    badge_rect = pg.Rect(0, 0, text_rect.width + padding_x, text_rect.height + padding_y)
    badge_rect.center = (center_x, center_y)

    badge_rect.clamp_ip(pg.Rect(0, 0, BOARD_WIDTH, BOARD_HEIGHT))

    pg.draw.rect(screen, bg_color, badge_rect, border_radius=10)

    text_rect.center = badge_rect.center
    screen.blit(text_surface, text_rect)

if __name__ == '__main__':
    main()