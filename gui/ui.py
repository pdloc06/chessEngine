"""
Handles overlays, UI panels, move logs, and game over states.

This module is responsible for rendering all non-board graphical interfaces,
including the move history panel, control buttons, the pawn promotion menu,
and endgame badges.
"""
import pygame as pg
import chess_engine, config


def get_control_button_rects() -> tuple[pg.Rect, pg.Rect, pg.Rect, pg.Rect]:
    """
    Calculate and return the bounding rectangles for all UI control buttons.

    Returns
    -------
    tuple
        A tuple of four pygame.Rect objects: (prev_btn, next_btn, restart_btn, flip_btn).
    """
    prev_btn = pg.Rect(config.BOARD_WIDTH + 10, config.BOARD_HEIGHT - 80, (config.MOVE_LOG_PANEL_WIDTH - 30) // 2, 30)
    next_btn = pg.Rect(config.BOARD_WIDTH + 20 + prev_btn.width, config.BOARD_HEIGHT - 80, prev_btn.width, 30)
    restart_btn = pg.Rect(config.BOARD_WIDTH + 10, config.BOARD_HEIGHT - 40, config.MOVE_LOG_PANEL_WIDTH - 70, 30)
    flip_btn = pg.Rect(config.BOARD_WIDTH + config.MOVE_LOG_PANEL_WIDTH - 50, config.BOARD_HEIGHT - 40, 40, 30)

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
    gs : chess_engine.GameState
        The current game state instance containing the move history.
    font : pygame.font.Font
        The font object utilized for rendering the log text.

    Returns
    -------
    None
    """
    panel_rect = pg.Rect(config.BOARD_WIDTH, 0, config.MOVE_LOG_PANEL_WIDTH, config.MOVE_LOG_PANEL_HEIGHT)
    pg.draw.rect(screen, pg.Color('#262421'), panel_rect)

    move_texts = []
    for i in range(0, len(gs.move_log), 2):
        move_string = str(i // 2 + 1) + "."
        white_move = gs.move_log[i].get_chess_notation()
        black_move = gs.move_log[i + 1].get_chess_notation() if (i + 1) < len(gs.move_log) else ""
        move_texts.append((move_string, white_move, black_move))

    text_y = 5
    line_spacing = 6  # Spacing to accommodate the selection box
    item_height = font.get_height() + line_spacing

    # Calculate maximum visible lines, subtracting 120 to leave safe space for buttons
    max_lines = (config.BOARD_HEIGHT - 120) // item_height
    start_line = max(0, len(move_texts) - max_lines)

    # Get the index of the currently active half-move
    current_move_index = len(gs.move_log) - 1

    # Only draw the lines that fit within the visible window
    for i in range(start_line, start_line + max_lines):
        if i >= len(move_texts): break
        num_str, w_move, b_move = move_texts[i]

        # Draw alternating row background colors (Zebra striping)
        row_color = pg.Color('#2b2927') if i % 2 == 0 else pg.Color('#262421')
        row_rect = pg.Rect(config.BOARD_WIDTH, text_y, config.MOVE_LOG_PANEL_WIDTH, item_height)
        pg.draw.rect(screen, row_color, row_rect)

        # Define specific x-coordinates for alignment
        num_x, w_x, b_x = config.BOARD_WIDTH + 15, config.BOARD_WIDTH + 65, config.BOARD_WIDTH + 155

        # Vertical offset to center text perfectly within the row's height
        text_offset_y = text_y + (line_spacing // 2)

        num_surface = font.render(num_str, True, pg.Color('#989795'))
        screen.blit(num_surface, (num_x, text_offset_y))

        # Draw White's move notation
        is_w_selected = (i * 2 == current_move_index)
        if is_w_selected:
            # Draw the selection box for the active move
            w_bg_rect = pg.Rect(w_x - 5, text_y, 80, item_height)
            pg.draw.rect(screen, pg.Color('#4c4a48'), w_bg_rect, border_radius=5)
            w_color = pg.Color('white')
        else:
            w_color = pg.Color('#c9c8c7')  # Dimmer text for inactive moves

        w_surface = font.render(w_move, True, w_color)
        screen.blit(w_surface, (w_x, text_offset_y))

        # Draw Black's move notation
        if b_move:
            is_b_selected = (i * 2 + 1 == current_move_index)
            if is_b_selected:
                # Draw the selection box for the active move
                b_bg_rect = pg.Rect(b_x - 5, text_y, 80, item_height)
                pg.draw.rect(screen, pg.Color('#4c4a48'), b_bg_rect, border_radius=5)
                b_color = pg.Color('white')
            else:
                b_color = pg.Color('#c9c8c7')

            b_surface = font.render(b_move, True, b_color)
            screen.blit(b_surface, (b_x, text_offset_y))

        text_y += item_height

    # Rendering match end states (Checkmate/Stalemate)
    if gs.is_stalemate:
        end_surface = font.render("1/2-1/2", True, pg.Color('#989795'))
        screen.blit(end_surface, (config.BOARD_WIDTH + 30, text_y))
    elif gs.is_checkmate:
        end_text = "0-1" if gs.white_to_move else "1-0"
        end_surface = font.render(end_text, True, pg.Color('#989795'))
        screen.blit(end_surface, (config.BOARD_WIDTH + 15, text_y))

    # Control buttons layout
    btn_color, text_color = pg.Color('#3c3a38'), pg.Color('white')
    prev_btn, next_btn, restart_btn, flip_btn = get_control_button_rects()

    for btn, txt in zip([prev_btn, next_btn, restart_btn, flip_btn], ["<", ">", "Restart Game", "Flip"]):
        pg.draw.rect(screen, btn_color, btn, border_radius=5)
        text_surf = font.render(txt, True, text_color)
        screen.blit(text_surf, text_surf.get_rect(center=btn.center))

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
    # Flip the index plane if the board is flipped
    end_draw_row = (7 - move.end_row) if board_flipped else move.end_row
    draw_col = (7 - move.end_col) if board_flipped else move.end_col

    # Direction dictates whether the menu spawns downwards (top of board) or upwards (bottom)
    direction = 1 if end_draw_row < 4 else -1
    rects = []

    # Calculate piece bounding boxes
    for i, p in enumerate(['Q', 'N', 'R', 'B']):
        row = end_draw_row + i * direction
        rect = pg.Rect(draw_col * config.SQ_SIZE, row * config.SQ_SIZE, config.SQ_SIZE, config.SQ_SIZE)
        rects.append((rect, p))

    # Calculate coordinates for the cancel 'x' button (Half size)
    x_height = config.SQ_SIZE // 2
    if direction == 1:
        x_y, menu_y = (end_draw_row + 4) * config.SQ_SIZE, end_draw_row * config.SQ_SIZE
    else:
        # If spawning upwards, 'x' sits directly above the top piece
        x_y = (end_draw_row - 3) * config.SQ_SIZE - x_height
        menu_y = x_y

    rects.append((pg.Rect(draw_col * config.SQ_SIZE, x_y, config.SQ_SIZE, x_height), 'x'))

    # Background and shadow calculations
    menu_bg_rect = pg.Rect(draw_col * config.SQ_SIZE, menu_y, config.SQ_SIZE, 4 * config.SQ_SIZE + x_height)

    shadow_offset = 6
    shadow_rect = pg.Rect(
        menu_bg_rect.x + shadow_offset, menu_bg_rect.y + shadow_offset,
        menu_bg_rect.width, menu_bg_rect.height
    )

    return menu_bg_rect, shadow_rect, rects

def draw_promotion_menu(
    screen: pg.Surface,
    move: chess_engine.Move,
    board_flipped: bool,
    font: pg.font.Font
) -> None:
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
        The font object used to render the 'x' cancel text.

    Returns
    -------
    None
    """
    color = 'w' if move.piece_moved[0] == 'w' else 'b'
    menu_bg_rect, shadow_rect, menu_rects = get_promotion_menu_rects(move, board_flipped)

    # Draw Drop Shadow (Fake blur using semi-transparent black surface)
    shadow_surface = pg.Surface((shadow_rect.width, shadow_rect.height), pg.SRCALPHA)
    # Using pg.draw.rect to round the shadow (border_radius=5 sync with the background)
    pg.draw.rect(shadow_surface, (0, 0, 0, 80), shadow_surface.get_rect(), border_radius=10)
    screen.blit(shadow_surface, shadow_rect.topleft)

    # Draw Menu Background (Matches the move log panel for consistency)
    pg.draw.rect(screen, pg.Color('#3c3a38'), menu_bg_rect, border_radius=10)

    # Draw a thin subtle border around the entire menu for extra sharpness
    pg.draw.rect(screen, pg.Color('#5c5a58'), menu_bg_rect, 1, border_radius=10)

    # Calculate direction to draw the 'x' button separator line correctly
    direction = 1 if ((7 - move.end_row) if board_flipped else move.end_row) < 4 else -1

    # Draw Pieces and Cancel Button
    for rect, option in menu_rects:
        if option == 'x':
            # Draw a subtle separator line above or below the 'x' button based on direction
            line_y = rect.top if direction == 1 else rect.bottom
            pg.draw.line(
                screen, pg.Color('#5c5a58'),
                (menu_bg_rect.left, line_y),
                (menu_bg_rect.right, line_y),
                2
            )

            # Render 'x' character in the center of its half-height rect
            text = font.render('x', True, pg.Color('#989795'))
            screen.blit(text, text.get_rect(center=rect.center))
        else:
            # Draw the piece directly on the dark background
            screen.blit(config.IMAGES[color + option], rect)

def winning_animation(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    white_wins: bool,
    board_flipped: bool
) -> None:
    """
    Render visual indicators and badges when the game ends in checkmate.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : chess_engine.GameState
        The game state containing king location parameters.
    white_wins : bool
        Flag signifying whether white executes the winning attack.
    board_flipped : bool
        Flag indicating if the board view is swapped.

    Returns
    -------
    None
    """
    win_loc = gs.white_king_location if white_wins else gs.black_king_location
    lose_loc = gs.black_king_location if white_wins else gs.white_king_location

    win_r_draw, win_c_draw = (7 - win_loc[0] if board_flipped else win_loc[0]), (7 - win_loc[1] if board_flipped else win_loc[1])
    lose_r_draw, lose_c_draw = (7 - lose_loc[0] if board_flipped else lose_loc[0]), (7 - lose_loc[1] if board_flipped else lose_loc[1])

    # Draw colored overlays on kings
    for loc, rgb in [((lose_c_draw, lose_r_draw), (255, 0, 0, 150)), ((win_c_draw, win_r_draw), (100, 200, 100, 150))]:
        surf = pg.Surface((config.SQ_SIZE, config.SQ_SIZE), pg.SRCALPHA)
        surf.fill(rgb)
        screen.blit(surf, (loc[0] * config.SQ_SIZE, loc[1] * config.SQ_SIZE))

    draw_badge(screen, "Winner", pg.Color('white'), pg.Color('green'), win_c_draw * config.SQ_SIZE + config.SQ_SIZE, win_r_draw * config.SQ_SIZE)
    draw_badge(screen, "Checkmate", pg.Color('red'), pg.Color('white'), lose_c_draw * config.SQ_SIZE + config.SQ_SIZE, lose_r_draw * config.SQ_SIZE)

def stalemate_animation(screen: pg.Surface, gs: chess_engine.GameState, board_flipped: bool) -> None:
    """
    Render visual indicators and badges when the game ends in a draw.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : chess_engine.GameState
        The game state tracking both kings.
    board_flipped : bool
        Flag dictating whether the board rendering is reversed.

    Returns
    -------
    None
    """
    gray_surface = pg.Surface((config.SQ_SIZE, config.SQ_SIZE), pg.SRCALPHA)
    gray_surface.fill((150, 150, 150, 150))

    for king_location in [gs.white_king_location, gs.black_king_location]:
        r_draw = 7 - king_location[0] if board_flipped else king_location[0]
        c_draw = 7 - king_location[1] if board_flipped else king_location[1]

        screen.blit(gray_surface, (c_draw * config.SQ_SIZE, r_draw * config.SQ_SIZE))
        badge_x = c_draw * config.SQ_SIZE + config.SQ_SIZE
        badge_y = r_draw * config.SQ_SIZE
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

    Returns
    -------
    None
    """
    font = pg.font.SysFont('Helvetica', 14, bold=True)
    text_surface = font.render(text, True, text_color)
    text_rect = text_surface.get_rect()

    padding_x, padding_y = 12, 6
    badge_rect = pg.Rect(0, 0, text_rect.width + padding_x, text_rect.height + padding_y)
    badge_rect.center = (center_x, center_y)

    # Ensure badge does not render outside the board bounds
    badge_rect.clamp_ip(pg.Rect(0, 0, config.BOARD_WIDTH, config.BOARD_HEIGHT))

    pg.draw.rect(screen, bg_color, badge_rect, border_radius=10)
    text_rect.center = badge_rect.center
    screen.blit(text_surface, text_rect)