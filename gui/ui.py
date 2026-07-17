"""
Handles overlays, UI panels, move logs, and game over states.

This module is responsible for rendering all non-board graphical interfaces,
including the main menu, the player info bars, the move history panel,
control buttons, the pawn promotion menu, and endgame badges.
"""
import pygame as pg
import config
from engine import chess_engine
from gui import graphics

# Layout constants shared by draw_move_log and get_move_log_click_index so the
# rendering and the click hit-testing can never drift apart
LOG_TOP_PADDING = 5
LOG_LINE_SPACING = 6
LOG_RESERVED_BOTTOM = 120  # Safe space reserved for the control buttons

# Standard chess point values (not the engine's centipawn tuning weights in
# move_finder.PIECE_VALUES) used only to size the "+N" material lead badge
POINT_VALUES: dict[str, int] = {'Q': 9, 'R': 5, 'B': 3, 'N': 3, 'P': 1}
STARTING_PIECE_COUNTS: dict[str, int] = {'P': 8, 'N': 2, 'B': 2, 'R': 2, 'Q': 1}

CLOCK_LOW_SECONDS = 20  # Below this, the ticking clock turns red


def compute_captured_material(board: list[list[str]]) -> tuple[dict[str, list[str]], int]:
    """
    Work out which pieces each side has captured and who is ahead on points.

    A piece type is "missing" from one color's set relative to the standard
    starting count exactly when the other color has captured one — comparing
    on-board counts avoids needing a separate capture log. Promotions are a
    minor wrinkle (a promoted queen can push a color's queen count above the
    start value); it's clamped at zero and reads as a harmless approximation
    rather than a phantom capture.

    Parameters
    ----------
    board : list of list of str
        The 2D board array populated with piece strings.

    Returns
    -------
    tuple
        A tuple of (captured, material_diff): `captured` maps 'w'/'b' to the
        list of opponent piece-type letters that color has captured (most
        valuable first); `material_diff` is White's points minus Black's
        (positive favors White).
    """
    on_board = {'w': dict.fromkeys(STARTING_PIECE_COUNTS, 0), 'b': dict.fromkeys(STARTING_PIECE_COUNTS, 0)}
    for row in board:
        for piece in row:
            if piece == '--':
                continue
            color, kind = piece[0], piece[1]
            if kind in on_board[color]:
                on_board[color][kind] += 1

    captured: dict[str, list[str]] = {'w': [], 'b': []}
    for kind, start_count in STARTING_PIECE_COUNTS.items():
        captured['w'].extend([kind] * max(0, start_count - on_board['b'][kind]))
        captured['b'].extend([kind] * max(0, start_count - on_board['w'][kind]))

    for color_captures in captured.values():
        color_captures.sort(key=lambda k: POINT_VALUES[k], reverse=True)

    white_points = sum(POINT_VALUES[k] for k in captured['w'])
    black_points = sum(POINT_VALUES[k] for k in captured['b'])
    return captured, white_points - black_points


def format_clock(seconds: float) -> str:
    """
    Format a remaining-time value in seconds as an "M:SS" clock string.

    Parameters
    ----------
    seconds : float
        Remaining time in seconds (never displayed as negative).

    Returns
    -------
    str
        The clock text, e.g. "9:07".
    """
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


def get_control_button_rects() -> tuple[pg.Rect, pg.Rect, pg.Rect, pg.Rect]:
    """
    Calculate and return the bounding rectangles for all UI control buttons.

    Returns
    -------
    tuple
        A tuple of four pygame.Rect objects: (prev_btn, next_btn, restart_btn, flip_btn).
    """
    prev_btn = pg.Rect(config.BOARD_WIDTH + 10, config.HEIGHT - 80, (config.MOVE_LOG_PANEL_WIDTH - 30) // 2, 30)
    next_btn = pg.Rect(config.BOARD_WIDTH + 20 + prev_btn.width, config.HEIGHT - 80, prev_btn.width, 30)
    restart_btn = pg.Rect(config.BOARD_WIDTH + 10, config.HEIGHT - 40, config.MOVE_LOG_PANEL_WIDTH - 70, 30)
    flip_btn = pg.Rect(config.BOARD_WIDTH + config.MOVE_LOG_PANEL_WIDTH - 50, config.HEIGHT - 40, 40, 30)

    return prev_btn, next_btn, restart_btn, flip_btn


def get_menu_button_rects() -> tuple[pg.Rect, pg.Rect]:
    """
    Calculate the bounding rectangles for the main menu mode buttons.

    Returns
    -------
    tuple
        A tuple of two pygame.Rect objects: (vs_ai_btn, two_players_btn).
    """
    btn_width, btn_height, gap = 280, 52, 18
    center_x = config.WIDTH // 2
    first_y = config.HEIGHT // 2 - btn_height - gap // 2 + 30

    vs_ai_btn = pg.Rect(0, 0, btn_width, btn_height)
    vs_ai_btn.center = (center_x, first_y)

    two_players_btn = pg.Rect(0, 0, btn_width, btn_height)
    two_players_btn.center = (center_x, first_y + btn_height + gap)

    return vs_ai_btn, two_players_btn


# Top of the button list, left fixed (rather than vertically centered on the
# full window) so it always clears the title/subtitle header above it
TIME_CONTROL_LIST_TOP = 190


def get_time_control_button_rects() -> list[tuple[pg.Rect, str]]:
    """
    Calculate the bounding rectangles for the time-control menu buttons.

    Returns
    -------
    list of tuple
        One (pg.Rect, mode_name) pair per entry in `config.GAME_MODES`, in
        the same order, stacked vertically below the menu header.
    """
    btn_width, btn_height, gap = 280, 48, 14
    center_x = config.WIDTH // 2
    modes = list(config.GAME_MODES.keys())

    rects = []
    for i, mode in enumerate(modes):
        rect = pg.Rect(0, 0, btn_width, btn_height)
        rect.top = TIME_CONTROL_LIST_TOP + i * (btn_height + gap)
        rect.centerx = center_x
        rects.append((rect, mode))
    return rects


def draw_time_control_menu(
    screen: pg.Surface,
    title_font: pg.font.Font,
    button_font: pg.font.Font,
    mouse_pos: tuple[int, int]
) -> None:
    """
    Draw the menu where the player picks a time control (or no clock at all).

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    title_font : pygame.font.Font
        Large font used for the game title.
    button_font : pygame.font.Font
        Font used for the button labels and the subtitle.
    mouse_pos : tuple of int
        Current mouse position, used for button hover feedback.

    Returns
    -------
    None
    """
    screen.fill(config.THEME['panel_bg'])

    title_surf = title_font.render('PyCheckmate', True, config.THEME['text'])
    screen.blit(title_surf, title_surf.get_rect(center=(config.WIDTH // 2, 80)))

    subtitle_surf = button_font.render('Choose a time control', True, config.THEME['text_muted'])
    screen.blit(subtitle_surf, subtitle_surf.get_rect(center=(config.WIDTH // 2, 130)))

    for rect, mode in get_time_control_button_rects():
        hovered = rect.collidepoint(mouse_pos)
        btn_color = config.THEME['button_hover'] if hovered else config.THEME['button']
        pg.draw.rect(screen, btn_color, rect, border_radius=8)
        pg.draw.rect(screen, config.THEME['border'], rect, 1, border_radius=8)

        initial_seconds, increment = config.GAME_MODES[mode]
        label = mode if initial_seconds is None else f"{mode} ({initial_seconds // 60}+{increment})"
        text_surf = button_font.render(label, True, config.THEME['text'])
        screen.blit(text_surf, text_surf.get_rect(center=rect.center))


def draw_main_menu(
    screen: pg.Surface,
    title_font: pg.font.Font,
    button_font: pg.font.Font,
    mouse_pos: tuple[int, int]
) -> None:
    """
    Draw the start menu where the player chooses the opponent type.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    title_font : pygame.font.Font
        Large font used for the game title.
    button_font : pygame.font.Font
        Font used for the button labels and the subtitle.
    mouse_pos : tuple of int
        Current mouse position, used for button hover feedback.

    Returns
    -------
    None
    """
    screen.fill(config.THEME['panel_bg'])

    # Title and subtitle block
    title_surf = title_font.render('PyCheckmate', True, config.THEME['text'])
    screen.blit(title_surf, title_surf.get_rect(center=(config.WIDTH // 2, config.HEIGHT // 3 - 20)))

    subtitle_surf = button_font.render('Choose your opponent', True, config.THEME['text_muted'])
    screen.blit(subtitle_surf, subtitle_surf.get_rect(center=(config.WIDTH // 2, config.HEIGHT // 3 + 30)))

    vs_ai_btn, two_players_btn = get_menu_button_rects()
    for rect, label in ((vs_ai_btn, 'Play vs Computer'), (two_players_btn, 'Two Players')):
        hovered = rect.collidepoint(mouse_pos)
        btn_color = config.THEME['button_hover'] if hovered else config.THEME['button']
        pg.draw.rect(screen, btn_color, rect, border_radius=8)
        pg.draw.rect(screen, config.THEME['border'], rect, 1, border_radius=8)

        text_surf = button_font.render(label, True, config.THEME['text'])
        screen.blit(text_surf, text_surf.get_rect(center=rect.center))


def draw_player_bars(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    font: pg.font.Font,
    board_flipped: bool,
    vs_ai: bool,
    ai_thinking: bool,
    clocks: dict[str, float] | None = None
) -> None:
    """
    Draw the top and bottom player info bars framing the board.

    Player 1 is always the bottom bar and always plays the color shown at the
    bottom of the board (White normally, Black when the board is flipped).
    The opponent occupies the top bar and is labelled "Computer" in AI mode.
    Each bar also shows that player's captured-material row (opponent piece
    icons, plus a "+N" badge for whoever is ahead on points) and, when a time
    control is active, a clock box in place of the old "to move" status text.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : chess_engine.GameState
        The current game state (used for the turn indicator and material).
    font : pygame.font.Font
        Font for player names, the material badge, and clock text.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    vs_ai : bool
        True when the opponent is the AI move finder.
    ai_thinking : bool
        True while the AI search is running (shows a "thinking..." status).
    clocks : dict of str to float, optional
        Remaining seconds keyed by 'w'/'b', or None when playing untimed. When
        given, each bar shows a clock box instead of the "to move" text.

    Returns
    -------
    None
    """
    player_one_color = 'b' if board_flipped else 'w'
    player_two_color = 'w' if board_flipped else 'b'

    top_rect = pg.Rect(0, 0, config.BOARD_WIDTH, config.PLAYER_BAR_HEIGHT)
    bottom_rect = pg.Rect(0, config.BOARD_TOP + config.BOARD_HEIGHT, config.BOARD_WIDTH, config.PLAYER_BAR_HEIGHT)

    bars = (
        (top_rect, 'Computer' if vs_ai else 'Player 2', player_two_color),
        (bottom_rect, 'Player 1', player_one_color),
    )

    captured, material_diff = compute_captured_material(gs.board)

    for rect, name, color in bars:
        is_active = gs.friendly_color == color
        bar_color = config.THEME['bar_active'] if is_active else config.THEME['bar_bg']
        pg.draw.rect(screen, bar_color, rect)

        # Color swatch indicating which pieces this player commands
        swatch = pg.Rect(rect.x + 12, rect.y + (rect.height - 18) // 2, 18, 18)
        pg.draw.rect(screen, pg.Color('white') if color == 'w' else pg.Color('black'), swatch, border_radius=4)
        pg.draw.rect(screen, config.THEME['border'], swatch, 1, border_radius=4)

        name_surf = font.render(name, True, config.THEME['text'] if is_active else config.THEME['text_dim'])
        screen.blit(name_surf, (swatch.right + 10, rect.centery - name_surf.get_height() // 2))

        # Material row: icons of the opponent pieces this player has
        # captured, in the opponent's color, followed by the point lead
        opponent_color = 'b' if color == 'w' else 'w'
        icon_x = swatch.right + 10 + name_surf.get_width() + 14
        icon_size = config.CAPTURED_ICON_SIZE
        icon_y = rect.centery - icon_size // 2
        for kind in captured[color]:
            screen.blit(config.SMALL_IMAGES[opponent_color + kind], (icon_x, icon_y))
            icon_x += icon_size + 2

        is_leading = (material_diff > 0 and color == 'w') or (material_diff < 0 and color == 'b')
        if is_leading:
            lead_surf = font.render(f"+{abs(material_diff)}", True, config.THEME['accent'])
            screen.blit(lead_surf, (icon_x + 4, rect.centery - lead_surf.get_height() // 2))

        # Right-aligned: a live clock box when timed, otherwise the old
        # turn/thinking status text
        if clocks is not None:
            remaining = clocks[color]
            is_low = remaining <= CLOCK_LOW_SECONDS
            clock_box = pg.Rect(0, 0, 64, 26)
            clock_box.midright = (rect.right - 12, rect.centery)

            if is_active and is_low:
                box_color, text_color = config.THEME['clock_low_bg'], pg.Color('white')
            elif is_active:
                box_color, text_color = config.THEME['clock_active_bg'], pg.Color('#1f1d1b')
            else:
                box_color, text_color = config.THEME['clock_bg'], config.THEME['text_dim']

            pg.draw.rect(screen, box_color, clock_box, border_radius=6)
            time_surf = font.render(format_clock(remaining), True, text_color)
            screen.blit(time_surf, time_surf.get_rect(center=clock_box.center))
        elif is_active:
            status = 'thinking...' if (ai_thinking and vs_ai and name == 'Computer') else 'to move'
            status_surf = font.render(status, True, config.THEME['accent'])
            screen.blit(status_surf, (rect.right - status_surf.get_width() - 12,
                                      rect.centery - status_surf.get_height() // 2))


def time_forfeit_banner(screen: pg.Surface, flag_fallen_color: str) -> None:
    """
    Render a centered banner announcing that a player's clock has run out.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    flag_fallen_color : str
        The color ('w' or 'b') whose clock reached zero.

    Returns
    -------
    None
    """
    loser = 'White' if flag_fallen_color == 'w' else 'Black'
    winner = 'Black' if flag_fallen_color == 'w' else 'White'
    center_x = config.BOARD_WIDTH // 2
    center_y = config.BOARD_TOP + config.BOARD_HEIGHT // 2
    draw_badge(screen, f"{loser} flags, {winner} wins", pg.Color('white'), pg.Color('black'), center_x, center_y)


def draw_move_log(
    screen: pg.Surface,
    gs: chess_engine.GameState,
    font: pg.font.Font,
    forced_result: str | None = None
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
    forced_result : str, optional
        Pre-computed result string ("1-0"/"0-1") to show instead of checking
        `gs.is_checkmate`/`gs.is_stalemate`, used when the game ends on a
        clock flag rather than a rules-driven end state.

    Returns
    -------
    None
    """
    panel_rect = pg.Rect(config.BOARD_WIDTH, 0, config.MOVE_LOG_PANEL_WIDTH, config.MOVE_LOG_PANEL_HEIGHT)
    pg.draw.rect(screen, config.THEME['panel_bg'], panel_rect)

    move_texts = []
    for i in range(0, len(gs.move_log), 2):
        move_string = str(i // 2 + 1) + "."
        white_move = gs.move_log[i].get_chess_notation()
        black_move = gs.move_log[i + 1].get_chess_notation() if (i + 1) < len(gs.move_log) else ""
        move_texts.append((move_string, white_move, black_move))

    text_y = LOG_TOP_PADDING
    item_height = font.get_height() + LOG_LINE_SPACING

    # Calculate maximum visible lines, leaving safe space for buttons
    max_lines = (config.MOVE_LOG_PANEL_HEIGHT - LOG_RESERVED_BOTTOM) // item_height
    start_line = max(0, len(move_texts) - max_lines)

    # Get the index of the currently active half-move
    current_move_index = len(gs.move_log) - 1

    # Only draw the lines that fit within the visible window
    for i in range(start_line, start_line + max_lines):
        if i >= len(move_texts): break
        num_str, w_move, b_move = move_texts[i]

        # Draw alternating row background colors (Zebra striping)
        row_color = config.THEME['panel_row'] if i % 2 == 0 else config.THEME['panel_bg']
        row_rect = pg.Rect(config.BOARD_WIDTH, text_y, config.MOVE_LOG_PANEL_WIDTH, item_height)
        pg.draw.rect(screen, row_color, row_rect)

        # Define specific x-coordinates for alignment
        num_x, w_x, b_x = config.BOARD_WIDTH + 15, config.BOARD_WIDTH + 65, config.BOARD_WIDTH + 155

        # Vertical offset to center text perfectly within the row's height
        text_offset_y = text_y + (LOG_LINE_SPACING // 2)

        num_surface = font.render(num_str, True, config.THEME['text_muted'])
        screen.blit(num_surface, (num_x, text_offset_y))

        # Draw White's move notation
        is_w_selected = (i * 2 == current_move_index)
        if is_w_selected:
            # Draw the selection box for the active move
            w_bg_rect = pg.Rect(w_x - 5, text_y, 80, item_height)
            pg.draw.rect(screen, config.THEME['panel_select'], w_bg_rect, border_radius=5)
            w_color = config.THEME['text']
        else:
            w_color = config.THEME['text_dim']  # Dimmer text for inactive moves

        w_surface = font.render(w_move, True, w_color)
        screen.blit(w_surface, (w_x, text_offset_y))

        # Draw Black's move notation
        if b_move:
            is_b_selected = (i * 2 + 1 == current_move_index)
            if is_b_selected:
                # Draw the selection box for the active move
                b_bg_rect = pg.Rect(b_x - 5, text_y, 80, item_height)
                pg.draw.rect(screen, config.THEME['panel_select'], b_bg_rect, border_radius=5)
                b_color = config.THEME['text']
            else:
                b_color = config.THEME['text_dim']

            b_surface = font.render(b_move, True, b_color)
            screen.blit(b_surface, (b_x, text_offset_y))

        text_y += item_height

    # Rendering match end states (Checkmate/Stalemate/clock flag)
    if forced_result is not None:
        end_surface = font.render(forced_result, True, config.THEME['text_muted'])
        screen.blit(end_surface, (config.BOARD_WIDTH + 15, text_y))
    elif gs.is_stalemate:
        end_surface = font.render("1/2-1/2", True, config.THEME['text_muted'])
        screen.blit(end_surface, (config.BOARD_WIDTH + 30, text_y))
    elif gs.is_checkmate:
        end_text = "0-1" if gs.white_to_move else "1-0"
        end_surface = font.render(end_text, True, config.THEME['text_muted'])
        screen.blit(end_surface, (config.BOARD_WIDTH + 15, text_y))

    # Control buttons layout
    btn_color, text_color = config.THEME['button'], config.THEME['text']
    prev_btn, next_btn, restart_btn, flip_btn = get_control_button_rects()

    for btn, txt in zip([prev_btn, next_btn, restart_btn, flip_btn], ["<", ">", "Restart Game", "Flip"]):
        pg.draw.rect(screen, btn_color, btn, border_radius=5)
        text_surf = font.render(txt, True, text_color)
        screen.blit(text_surf, text_surf.get_rect(center=btn.center))


def get_move_log_click_index(
    location: tuple[int, int],
    total_moves: int,
    font: pg.font.Font
) -> int | None:
    """
    Map a click inside the move-log panel to a half-move index.

    Uses the exact same layout constants as `draw_move_log`, so a click on a
    rendered notation line always resolves to that line's half-move.

    Parameters
    ----------
    location : tuple of int
        The (x, y) pixel coordinates of the click.
    total_moves : int
        The current number of half-moves in the game log.
    font : pygame.font.Font
        The font used by the move log (determines line height).

    Returns
    -------
    int or None
        The 0-based half-move index the click points at, or None when the
        click was outside the notation list area.
    """
    y_offset = location[1] - LOG_TOP_PADDING
    if not (0 < y_offset < config.MOVE_LOG_PANEL_HEIGHT - LOG_RESERVED_BOTTOM + 20):
        return None

    item_height = font.get_height() + LOG_LINE_SPACING
    max_lines = (config.MOVE_LOG_PANEL_HEIGHT - LOG_RESERVED_BOTTOM) // item_height
    start_line = max(0, ((total_moves + 1) // 2) - max_lines)

    # Calculate target index including the scrolled offset; the right column
    # (x beyond the White column area) selects Black's half-move
    return (start_line + y_offset // item_height) * 2 + (
        0 if location[0] < config.BOARD_WIDTH + 120 else 1
    )


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

    # Calculate piece bounding boxes (offset by the top player bar)
    for i, p in enumerate(['Q', 'N', 'R', 'B']):
        row = end_draw_row + i * direction
        rect = pg.Rect(
            draw_col * config.SQ_SIZE,
            config.BOARD_TOP + row * config.SQ_SIZE,
            config.SQ_SIZE, config.SQ_SIZE
        )
        rects.append((rect, p))

    # Calculate coordinates for the cancel 'x' button (Half size)
    x_height = config.SQ_SIZE // 2
    if direction == 1:
        x_y = config.BOARD_TOP + (end_draw_row + 4) * config.SQ_SIZE
        menu_y = config.BOARD_TOP + end_draw_row * config.SQ_SIZE
    else:
        # If spawning upwards, 'x' sits directly above the top piece
        x_y = config.BOARD_TOP + (end_draw_row - 3) * config.SQ_SIZE - x_height
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
    pg.draw.rect(screen, config.THEME['button'], menu_bg_rect, border_radius=10)

    # Draw a thin subtle border around the entire menu for extra sharpness
    pg.draw.rect(screen, config.THEME['border'], menu_bg_rect, 1, border_radius=10)

    # Calculate direction to draw the 'x' button separator line correctly
    direction = 1 if ((7 - move.end_row) if board_flipped else move.end_row) < 4 else -1

    # Draw Pieces and Cancel Button
    for rect, option in menu_rects:
        if option == 'x':
            # Draw a subtle separator line above or below the 'x' button based on direction
            line_y = rect.top if direction == 1 else rect.bottom
            pg.draw.line(
                screen, config.THEME['border'],
                (menu_bg_rect.left, line_y),
                (menu_bg_rect.right, line_y),
                2
            )

            # Render 'x' character in the center of its half-height rect
            text = font.render('x', True, config.THEME['text_muted'])
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

    win_x, win_y = graphics.board_to_screen(win_loc[0], win_loc[1], board_flipped)
    lose_x, lose_y = graphics.board_to_screen(lose_loc[0], lose_loc[1], board_flipped)

    # Draw colored overlays on kings
    for pos, rgb in [((lose_x, lose_y), (255, 0, 0, 150)), ((win_x, win_y), (100, 200, 100, 150))]:
        surf = pg.Surface((config.SQ_SIZE, config.SQ_SIZE), pg.SRCALPHA)
        surf.fill(rgb)
        screen.blit(surf, pos)

    draw_badge(screen, "Winner", pg.Color('white'), pg.Color('green'), win_x + config.SQ_SIZE, win_y)
    draw_badge(screen, "Checkmate", pg.Color('red'), pg.Color('white'), lose_x + config.SQ_SIZE, lose_y)

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
        x, y = graphics.board_to_screen(king_location[0], king_location[1], board_flipped)
        screen.blit(gray_surface, (x, y))
        draw_badge(screen, "Draw", pg.Color('white'), pg.Color('black'), x + config.SQ_SIZE, y)

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
    badge_rect.clamp_ip(pg.Rect(0, config.BOARD_TOP, config.BOARD_WIDTH, config.BOARD_HEIGHT))

    pg.draw.rect(screen, bg_color, badge_rect, border_radius=10)
    text_rect.center = badge_rect.center
    screen.blit(text_surface, text_rect)
