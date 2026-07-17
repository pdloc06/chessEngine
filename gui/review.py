"""
Rendering for the game-review feature (chess.com-style "Game Review").

This module draws everything the review screen adds on top of the normal
board rendering: the evaluation bar in the left gutter, the move list with
per-move quality icons, the quality badge on the moved piece, the engine's
best-move arrow, and the FEN/PGN import screen. Following the project
convention, only drawing and hit-testing live here — the event loops that
drive the review are in main.py.
"""
import subprocess
import sys

import pygame as pg
import config
from engine import analysis, chess_engine
from gui import graphics, ui

# Move-list layout (mirrors the spirit of ui.draw_move_log, but with an icon
# column squeezed in front of each move's text)
REVIEW_LOG_TOP = 56           # Below the panel header
REVIEW_LOG_LINE_SPACING = 6
REVIEW_LOG_RESERVED_BOTTOM = 130  # Safe space reserved for the nav buttons
VARIATION_BAR_HEIGHT = 30     # "Back to game" strip above a variation's moves

# A variation being explored on the review board, as the GUI passes it to
# the drawing/hit-testing functions: (base, moves, worker, cursor) — the
# mainline position index it branched from, its moves, its own analyser,
# and how many of its moves are currently applied to the board.
Variation = tuple[int, list[chess_engine.Move], analysis.GameAnalysis, int]

# X offsets inside the panel: move number, then icon+text per color column
_NUM_X = 8
_WHITE_ICON_X = 44
_BLACK_ICON_X = 144
_ICON_TEXT_GAP = 4

# Eval-bar colors: deliberately near-white/near-black so the bar reads as
# "how much of the game belongs to each side"
_BAR_WHITE = pg.Color('#f0f0f0')
_BAR_BLACK = pg.Color('#454341')


def panel_x() -> int:
    """Left edge of the review side panel (board offset by the eval bar)."""
    return config.BOARD_LEFT + config.BOARD_WIDTH


def get_review_button_rects() -> tuple[pg.Rect, pg.Rect, pg.Rect, pg.Rect]:
    """
    Calculate the bounding rectangles for the review screen's buttons.

    Returns
    -------
    tuple
        Four pygame.Rect objects: (exit_btn, prev_btn, next_btn, flip_btn).
    """
    x = panel_x()
    width = config.MOVE_LOG_PANEL_WIDTH
    exit_btn = pg.Rect(x + 10, config.HEIGHT - 120, width - 20, 30)
    prev_btn = pg.Rect(x + 10, config.HEIGHT - 80, (width - 30) // 2, 30)
    next_btn = pg.Rect(x + 20 + prev_btn.width, config.HEIGHT - 80, prev_btn.width, 30)
    flip_btn = pg.Rect(x + 10, config.HEIGHT - 40, width - 20, 30)
    return exit_btn, prev_btn, next_btn, flip_btn


def _log_window(
    total_lines: int,
    current_line: int,
    item_height: int,
    top_y: int,
) -> tuple[int, int]:
    """
    Work out which slice of move-list lines is visible.

    Keeps the line holding the currently displayed move inside the window
    (with one line of context below it) so navigation always stays in view.

    Parameters
    ----------
    total_lines : int
        Number of full-move lines in the log.
    current_line : int
        Index of the line holding the currently displayed half-move.
    item_height : int
        Pixel height of one line.
    top_y : int
        Where the list starts (lower when the variation strip is shown).

    Returns
    -------
    tuple of int
        (start_line, max_lines) of the visible window.
    """
    view_height = config.MOVE_LOG_PANEL_HEIGHT - top_y - REVIEW_LOG_RESERVED_BOTTOM
    max_lines = max(1, view_height // item_height)
    if total_lines <= max_lines:
        return 0, max_lines
    start = min(max(0, current_line - max_lines + 2), total_lines - max_lines)
    return start, max_lines


def get_variation_back_rect() -> pg.Rect:
    """
    Calculate the bounding rectangle of the "Back to game" strip.

    Shown above the move list while a variation is being explored; clicking
    it abandons the variation and returns to the mainline.

    Returns
    -------
    pg.Rect
        The strip's rect, spanning the width of the review panel.
    """
    return pg.Rect(
        panel_x() + 10, REVIEW_LOG_TOP,
        config.MOVE_LOG_PANEL_WIDTH - 20, VARIATION_BAR_HEIGHT - 6
    )


def _rows_geometry(
    moves_count: int,
    first_global: int,
    selected: int,
    item_height: int,
    top_y: int,
) -> tuple[int, int, int, int]:
    """
    Shared scroll math for the move rows (drawing and hit-testing).

    Rows are grouped into full-move lines by *global* half-move index, so a
    variation that starts on Black's move still lines up under its move
    number, with the White cell left blank.

    Parameters
    ----------
    moves_count : int
        Number of half-moves in the displayed list.
    first_global : int
        Global half-move index of the list's first move (0 for the
        mainline; the branch point for a variation).
    selected : int
        List-local index of the highlighted move, or -1 for none.
    item_height : int
        Pixel height of one line.
    top_y : int
        Where the list starts on screen.

    Returns
    -------
    tuple of int
        (first_line, total_lines, start_offset, max_lines) — the first
        global line number, how many lines the list spans, and the visible
        window within them.
    """
    first_line = first_global // 2
    last_global = first_global + max(moves_count, 1) - 1
    total_lines = last_global // 2 - first_line + 1 if moves_count else 0
    selected_line = (first_global + max(selected, 0)) // 2 - first_line
    start_offset, max_lines = _log_window(total_lines, selected_line, item_height, top_y)
    return first_line, total_lines, start_offset, max_lines


def _draw_move_rows(
    screen: pg.Surface,
    moves: list[chess_engine.Move],
    tags: list[str | None],
    selected: int,
    font: pg.font.Font,
    top_y: int,
    first_global: int,
) -> None:
    """
    Draw a list of tagged moves as numbered full-move rows.

    Used for both the mainline and a variation: the only difference is the
    global half-move index the list starts at.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    moves : list of chess_engine.Move
        The half-moves to render, in order.
    tags : list of str or None
        Quality tag per move (None while its analysis is pending).
    selected : int
        List-local index of the move to highlight, or -1 for none.
    font : pygame.font.Font
        Font for the rows.
    top_y : int
        Where the list starts on screen.
    first_global : int
        Global half-move index of `moves[0]` (0 for the mainline).

    Returns
    -------
    None
    """
    x = panel_x()
    item_height = font.get_height() + REVIEW_LOG_LINE_SPACING
    first_line, total_lines, start_offset, max_lines = _rows_geometry(
        len(moves), first_global, selected, item_height, top_y
    )

    text_y = top_y
    for line_offset in range(start_offset, min(start_offset + max_lines, total_lines)):
        line = first_line + line_offset
        row_color = config.THEME['panel_row'] if line % 2 == 0 else config.THEME['panel_bg']
        pg.draw.rect(
            screen, row_color,
            pg.Rect(x, text_y, config.MOVE_LOG_PANEL_WIDTH, item_height)
        )
        text_offset_y = text_y + REVIEW_LOG_LINE_SPACING // 2

        num_surf = font.render(f'{line + 1}.', True, config.THEME['text_muted'])
        screen.blit(num_surf, (x + _NUM_X, text_offset_y))

        for half, icon_x in ((0, _WHITE_ICON_X), (1, _BLACK_ICON_X)):
            index = line * 2 + half - first_global
            if not 0 <= index < len(moves):
                continue  # e.g. a variation starting on Black's half-move

            is_selected = index == selected
            if is_selected:
                select_rect = pg.Rect(x + icon_x - 4, text_y, 96, item_height)
                pg.draw.rect(screen, config.THEME['panel_select'], select_rect, border_radius=5)

            text_x = x + icon_x
            tag = tags[index] if index < len(tags) else None
            if tag is not None and tag in config.EVAL_ICONS_LOG:
                icon = config.EVAL_ICONS_LOG[tag]
                icon_y = text_y + (item_height - icon.get_height()) // 2
                screen.blit(icon, (text_x, icon_y))
                text_x += config.EVAL_ICON_LOG_SIZE + _ICON_TEXT_GAP

            color = config.THEME['text'] if is_selected else config.THEME['text_dim']
            move_surf = font.render(moves[index].get_chess_notation(), True, color)
            screen.blit(move_surf, (text_x, text_offset_y))

        text_y += item_height


def draw_review_panel(
    screen: pg.Surface,
    moves: list[chess_engine.Move],
    game_analysis: analysis.GameAnalysis,
    cursor: int,
    font: pg.font.Font,
    variation: Variation | None = None,
) -> None:
    """
    Draw the review side panel: header, tagged move list, and nav buttons.

    While a variation is being explored, the panel shows the variation's
    moves (with their own analysis) below a "Back to game" strip instead of
    the mainline list.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    moves : list of chess_engine.Move
        Every move of the reviewed game, in order.
    game_analysis : analysis.GameAnalysis
        The background analyser (provides tags, progress, and accuracy).
    cursor : int
        Number of mainline moves currently applied to the displayed board;
        the half-move `cursor - 1` is highlighted as active.
    font : pygame.font.Font
        Font for all panel text.
    variation : Variation, optional
        The variation being explored, if any (see the `Variation` alias).

    Returns
    -------
    None
    """
    x = panel_x()
    panel_rect = pg.Rect(x, 0, config.MOVE_LOG_PANEL_WIDTH, config.MOVE_LOG_PANEL_HEIGHT)
    pg.draw.rect(screen, config.THEME['panel_bg'], panel_rect)

    # Header: title plus either analysis progress or the final accuracies.
    # A variation in progress reports its own analyser instead.
    title_surf = font.render('Game Review', True, config.THEME['text'])
    screen.blit(title_surf, (x + 10, 8))
    active_analysis = variation[2] if variation is not None else game_analysis
    if not active_analysis.done:
        status = (
            f'Analyzing... {active_analysis.analysed_positions}'
            f'/{active_analysis.total_positions}'
        )
    elif variation is None:
        white_acc = game_analysis.accuracy(white=True)
        black_acc = game_analysis.accuracy(white=False)
        status = (
            f'Accuracy  W {white_acc:.1f}  ·  B {black_acc:.1f}'
            if white_acc is not None and black_acc is not None else ''
        )
    else:
        status = 'Exploring a variation'
    if status:
        status_surf = font.render(status, True, config.THEME['text_muted'])
        screen.blit(status_surf, (x + 10, 30))

    if variation is None:
        _draw_move_rows(
            screen, moves, game_analysis.tags, cursor - 1, font, REVIEW_LOG_TOP, 0
        )
    else:
        base, var_moves, var_worker, var_cursor = variation
        ui.draw_button(screen, get_variation_back_rect(), '< Back to game', font, radius=5)
        _draw_move_rows(
            screen, var_moves, var_worker.tags, var_cursor - 1, font,
            REVIEW_LOG_TOP + VARIATION_BAR_HEIGHT, base
        )

    # Navigation buttons
    exit_btn, prev_btn, next_btn, flip_btn = get_review_button_rects()
    for btn, label in (
        (exit_btn, '< Exit Review'),
        (prev_btn, '<'),
        (next_btn, '>'),
        (flip_btn, 'Flip Board'),
    ):
        ui.draw_button(screen, btn, label, font, radius=5)


def get_review_log_click_index(
    location: tuple[int, int],
    total_moves: int,
    cursor: int,
    font: pg.font.Font,
    variation: Variation | None = None,
) -> int | None:
    """
    Map a click inside the review move list to a half-move index.

    Uses the exact same layout math as `draw_review_panel`, so a click on a
    rendered move always resolves to that move. With a variation active the
    list shows the variation, so the returned index is variation-local.

    Parameters
    ----------
    location : tuple of int
        The (x, y) pixel coordinates of the click.
    total_moves : int
        The number of half-moves in the reviewed game's mainline.
    cursor : int
        Number of mainline moves currently applied (drives the scroll).
    font : pygame.font.Font
        The move-list font (determines line height).
    variation : Variation, optional
        The variation being explored, if any (must match the drawing call).

    Returns
    -------
    int or None
        The 0-based index of the clicked move in the *displayed* list
        (mainline or variation), or None when the click was outside it.
    """
    if variation is None:
        top_y, first_global, count, selected = REVIEW_LOG_TOP, 0, total_moves, cursor - 1
    else:
        base, var_moves, _, var_cursor = variation
        top_y = REVIEW_LOG_TOP + VARIATION_BAR_HEIGHT
        first_global, count, selected = base, len(var_moves), var_cursor - 1

    y_offset = location[1] - top_y
    if y_offset < 0 or count == 0:
        return None

    item_height = font.get_height() + REVIEW_LOG_LINE_SPACING
    first_line, total_lines, start_offset, max_lines = _rows_geometry(
        count, first_global, selected, item_height, top_y
    )

    line_offset = start_offset + y_offset // item_height
    if line_offset >= min(start_offset + max_lines, total_lines):
        return None

    half = 0 if location[0] < panel_x() + _BLACK_ICON_X else 1
    index = (first_line + line_offset) * 2 + half - first_global
    return index if 0 <= index < count else None


def draw_eval_bar(
    screen: pg.Surface,
    white_share: float,
    eval_text: str,
    board_flipped: bool,
    font: pg.font.Font,
) -> None:
    """
    Draw the vertical evaluation bar in the gutter left of the board.

    The bar splits into a white and a black section whose heights follow the
    win percentage, with White's section always on the side of the board
    where White sits (so flipping the board flips the bar too).

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    white_share : float
        White's expected-points share, 0.0-1.0 (0.5 = equal). Callers may
        smooth this value between frames for the chess.com-style glide.
    eval_text : str
        Label for the current evaluation, e.g. '+1.3' or 'M5'.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    font : pygame.font.Font
        Small font for the evaluation label.

    Returns
    -------
    None
    """
    # Gutter background, then the bar itself with a small margin
    gutter = pg.Rect(0, 0, config.BOARD_LEFT, config.HEIGHT)
    pg.draw.rect(screen, config.THEME['panel_bg'], gutter)

    margin = 6
    bar = pg.Rect(margin, config.BOARD_TOP, config.BOARD_LEFT - 2 * margin, config.BOARD_HEIGHT)
    share = min(1.0, max(0.0, white_share))
    white_height = round(bar.height * share)

    pg.draw.rect(screen, _BAR_BLACK, bar)
    white_at_bottom = not board_flipped
    if white_at_bottom:
        white_rect = pg.Rect(bar.x, bar.bottom - white_height, bar.width, white_height)
    else:
        white_rect = pg.Rect(bar.x, bar.y, bar.width, white_height)
    pg.draw.rect(screen, _BAR_WHITE, white_rect)
    pg.draw.rect(screen, config.THEME['border'], bar, 1)

    # The label sits at the leading side's end of the bar, colored to
    # contrast with that side's section
    white_leads = share >= 0.5
    text_color = _BAR_BLACK if white_leads else _BAR_WHITE
    label = font.render(eval_text, True, text_color)
    at_bottom = white_leads == white_at_bottom
    label_y = bar.bottom - label.get_height() - 3 if at_bottom else bar.y + 3
    screen.blit(label, label.get_rect(centerx=bar.centerx, y=label_y))


def draw_move_badge(
    screen: pg.Surface,
    move: chess_engine.Move,
    tag: str,
    board_flipped: bool,
) -> None:
    """
    Draw the quality badge on the destination square of the shown move.

    Mirrors chess.com's review board, where the tag icon sits on the top
    right corner of the square the piece landed on.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    move : chess_engine.Move
        The move being displayed on the board.
    tag : str
        The move's quality tag (an `engine.analysis` tag constant).
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    icon = config.EVAL_ICONS_BOARD.get(tag)
    if icon is None:
        return
    x, y = graphics.board_to_screen(move.end_row, move.end_col, board_flipped)
    size = config.EVAL_ICON_BOARD_SIZE
    screen.blit(icon, (x + config.SQ_SIZE - size - 1, y + 1))


def draw_end_badges(screen: pg.Surface, gs: chess_engine.GameState, board_flipped: bool) -> None:
    """
    Draw the end-of-game icons on the kings at the final position.

    Checkmate marks the mated king and crowns the winner; a stalemate or
    other draw marks both kings with the draw badge.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : chess_engine.GameState
        The final position (its checkmate/stalemate flags must be current).
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    size = config.EVAL_ICON_BOARD_SIZE

    def blit_on_king(location: tuple[int, int], icon_name: str) -> None:
        icon = config.EVAL_ICONS_BOARD.get(icon_name)
        if icon is not None:
            x, y = graphics.board_to_screen(location[0], location[1], board_flipped)
            screen.blit(icon, (x + config.SQ_SIZE - size - 1, y + 1))

    if gs.is_checkmate:
        mated_is_white = gs.white_to_move
        blit_on_king(
            gs.white_king_location if mated_is_white else gs.black_king_location,
            'checkmate_white' if mated_is_white else 'checkmate_black',
        )
        blit_on_king(
            gs.black_king_location if mated_is_white else gs.white_king_location,
            'winner',
        )
    elif gs.is_stalemate:
        blit_on_king(gs.white_king_location, 'draw_white')
        blit_on_king(gs.black_king_location, 'draw_black')


def draw_best_move_arrow(
    screen: pg.Surface,
    move_tuple: tuple[int, int, int, int, int],
    board_flipped: bool,
) -> None:
    """
    Draw a translucent arrow showing the engine's preferred move.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    move_tuple : tuple of int
        The engine move in the lightweight AI tuple format.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    start_row, start_col, end_row, end_col, _ = move_tuple
    half = config.SQ_SIZE // 2
    sx, sy = graphics.board_to_screen(start_row, start_col, board_flipped)
    ex, ey = graphics.board_to_screen(end_row, end_col, board_flipped)
    start = pg.math.Vector2(sx + half, sy + half)
    end = pg.math.Vector2(ex + half, ey + half)
    direction = end - start
    if direction.length() == 0:
        return
    direction = direction.normalize()

    # Draw on a transparent overlay so the arrow blends over the pieces
    overlay = pg.Surface(screen.get_size(), pg.SRCALPHA)
    arrow_color = (110, 175, 90, 160)  # Soft green, like analysis boards use

    head_length = config.SQ_SIZE * 0.45
    head_width = config.SQ_SIZE * 0.42
    shaft_width = config.SQ_SIZE // 6
    base = end - direction * head_length  # Where the shaft meets the head

    normal = pg.math.Vector2(-direction.y, direction.x)
    shaft = [
        start + normal * (shaft_width / 2),
        base + normal * (shaft_width / 2),
        base - normal * (shaft_width / 2),
        start - normal * (shaft_width / 2),
    ]
    head = [end, base + normal * (head_width / 2), base - normal * (head_width / 2)]
    pg.draw.polygon(overlay, arrow_color, [(p.x, p.y) for p in shaft])
    pg.draw.polygon(overlay, arrow_color, [(p.x, p.y) for p in head])
    screen.blit(overlay, (0, 0))


def draw_review_bars(screen: pg.Surface, gs: chess_engine.GameState, font: pg.font.Font,
                     board_flipped: bool) -> None:
    """
    Draw minimal top/bottom player bars for the review screen.

    The review has no players or clocks, so the bars just label the colors
    and mark whose turn the displayed position is.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    gs : chess_engine.GameState
        The displayed position.
    font : pygame.font.Font
        Font for the color labels.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    bottom_color = 'b' if board_flipped else 'w'
    top_color = 'w' if board_flipped else 'b'
    top_rect = pg.Rect(config.BOARD_LEFT, 0, config.BOARD_WIDTH, config.PLAYER_BAR_HEIGHT)
    bottom_rect = pg.Rect(
        config.BOARD_LEFT, config.BOARD_TOP + config.BOARD_HEIGHT,
        config.BOARD_WIDTH, config.PLAYER_BAR_HEIGHT
    )

    for rect, color in ((top_rect, top_color), (bottom_rect, bottom_color)):
        is_active = gs.friendly_color == color
        bar_color = config.THEME['bar_active'] if is_active else config.THEME['bar_bg']
        pg.draw.rect(screen, bar_color, rect)

        swatch = pg.Rect(rect.x + 12, rect.y + (rect.height - 18) // 2, 18, 18)
        pg.draw.rect(screen, pg.Color('white') if color == 'w' else pg.Color('black'), swatch,
                     border_radius=4)
        pg.draw.rect(screen, config.THEME['border'], swatch, 1, border_radius=4)

        name = 'White' if color == 'w' else 'Black'
        name_surf = font.render(name, True, config.THEME['text'] if is_active else config.THEME['text_dim'])
        screen.blit(name_surf, (swatch.right + 10, rect.centery - name_surf.get_height() // 2))

        if is_active:
            status_surf = font.render('to move', True, config.THEME['accent'])
            screen.blit(status_surf, (rect.right - status_surf.get_width() - 12,
                                      rect.centery - status_surf.get_height() // 2))


# ----------------------------------------------------------------------
# FEN / PGN import screen
# ----------------------------------------------------------------------
def get_import_button_rects() -> tuple[pg.Rect, pg.Rect, pg.Rect, pg.Rect]:
    """
    Calculate the bounding rectangles for the import screen's buttons.

    Returns
    -------
    tuple
        Four pygame.Rect objects: (back_btn, paste_btn, clear_btn, analyze_btn).
    """
    back_btn = pg.Rect(20, 20, 90, 34)
    box = get_import_text_box()
    paste_btn = pg.Rect(box.x, box.bottom + 12, 110, 32)
    clear_btn = pg.Rect(box.x + 120, box.bottom + 12, 110, 32)
    analyze_btn = pg.Rect(0, 0, 200, 44)
    analyze_btn.centerx = config.WIDTH // 2
    analyze_btn.top = box.bottom + 60
    return back_btn, paste_btn, clear_btn, analyze_btn


def get_import_text_box() -> pg.Rect:
    """Bounding rectangle of the FEN/PGN text area on the import screen."""
    return pg.Rect(40, 140, config.WIDTH - 80, 250)


def draw_import_menu(
    screen: pg.Surface,
    title_font: pg.font.Font,
    font: pg.font.Font,
    text: str,
    error: str | None,
    mouse_pos: tuple[int, int],
) -> None:
    """
    Draw the Game Analysis import screen (paste a FEN or a PGN).

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    title_font : pygame.font.Font
        Large font used for the screen title.
    font : pygame.font.Font
        Font for the text area, buttons, and messages.
    text : str
        The current contents of the input buffer.
    error : str or None
        A parse-failure message to display under the box, if any.
    mouse_pos : tuple of int
        Current mouse position, used for button hover feedback.

    Returns
    -------
    None
    """
    screen.fill(config.THEME['panel_bg'])

    title_surf = title_font.render('Game Analysis', True, config.THEME['text'])
    screen.blit(title_surf, title_surf.get_rect(center=(config.WIDTH // 2, 70)))
    subtitle = font.render(
        'Paste a FEN or a PGN, then hit Analyze', True, config.THEME['text_muted']
    )
    screen.blit(subtitle, subtitle.get_rect(center=(config.WIDTH // 2, 115)))

    # Text area with the tail of the buffer (long PGNs won't fit whole)
    box = get_import_text_box()
    pg.draw.rect(screen, config.THEME['bar_bg'], box, border_radius=6)
    pg.draw.rect(screen, config.THEME['border'], box, 1, border_radius=6)

    lines = _wrap_text(text, font, box.width - 20)
    line_height = font.get_height() + 2
    max_lines = (box.height - 16) // line_height
    visible = lines[-max_lines:] if len(lines) > max_lines else lines
    for i, line in enumerate(visible):
        line_surf = font.render(line, True, config.THEME['text'])
        screen.blit(line_surf, (box.x + 10, box.y + 8 + i * line_height))
    if not text:
        hint = font.render(
            'Type here, or use Paste (Cmd/Ctrl+V works too)', True, config.THEME['text_muted']
        )
        screen.blit(hint, (box.x + 10, box.y + 8))

    back_btn, paste_btn, clear_btn, analyze_btn = get_import_button_rects()
    for btn, label, accent in (
        (back_btn, '< Back', False),
        (paste_btn, 'Paste', False),
        (clear_btn, 'Clear', False),
        (analyze_btn, 'Analyze', True),
    ):
        ui.draw_button(screen, btn, label, font, mouse_pos, accent=accent, border=True)

    if error:
        error_surf = font.render(error, True, pg.Color('#e06c5c'))
        screen.blit(error_surf, error_surf.get_rect(
            centerx=config.WIDTH // 2, top=analyze_btn.bottom + 14
        ))


def _wrap_text(text: str, font: pg.font.Font, max_width: int) -> list[str]:
    """
    Break a multi-line string into rendered lines that fit `max_width`.

    Parameters
    ----------
    text : str
        The raw text (may contain newlines).
    font : pygame.font.Font
        Font that will render the lines (determines pixel widths).
    max_width : int
        Maximum pixel width of one rendered line.

    Returns
    -------
    list of str
        The wrapped lines, ready to render one under another.
    """
    lines: list[str] = []
    for raw_line in text.split('\n'):
        current = ''
        for word in raw_line.split(' '):
            candidate = f'{current} {word}'.strip()
            if current and font.size(candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def get_clipboard_text() -> str:
    """
    Fetch text from the system clipboard as robustly as possible.

    Tries pygame-ce's scrap API first, then falls back to the macOS
    `pbpaste` utility, so paste works even when SDL's clipboard support
    is unavailable.

    Returns
    -------
    str
        The clipboard contents, or an empty string when nothing readable
        is there.
    """
    try:
        text = pg.scrap.get_text()
        if text:
            return text
    except Exception:  # scrap can fail in many backend-specific ways
        pass
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=2)
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return ''
    return ''
