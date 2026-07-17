"""
Handles rendering the chess board, pieces, and interaction highlights.

This module isolates the graphical drawing routines for the physical board
elements, ensuring that game state visual representation is cleanly separated
from user input and UI overlays.

It also owns the two coordinate-translation helpers (`board_to_screen` and
`screen_to_board`) that every other module uses, so board flipping and the
player-bar offset are handled in exactly one place.
"""
from pathlib import Path
import pygame as pg
import config
from engine import chess_engine


def board_to_screen(row: int, col: int, board_flipped: bool) -> tuple[int, int]:
    """
    Convert logical board coordinates to on-screen pixel coordinates.

    Accounts for the board orientation (flip) and the vertical offset caused
    by the top player bar.

    Parameters
    ----------
    row : int
        The logical board row (0 = rank 8).
    col : int
        The logical board column (0 = file a).
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    tuple of int
        The (x, y) pixel coordinates of the square's top-left corner.
    """
    draw_row = (7 - row) if board_flipped else row
    draw_col = (7 - col) if board_flipped else col
    return draw_col * config.SQ_SIZE, config.BOARD_TOP + draw_row * config.SQ_SIZE


def screen_to_board(x: int, y: int, board_flipped: bool) -> tuple[int, int] | None:
    """
    Convert on-screen pixel coordinates to logical board coordinates.

    Parameters
    ----------
    x : int
        The horizontal pixel coordinate of the click.
    y : int
        The vertical pixel coordinate of the click.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    tuple of int or None
        The logical (row, col) of the clicked square, or None when the click
        landed outside the board area (player bars or move log panel).
    """
    if not (0 <= x < config.BOARD_WIDTH):
        return None
    if not (config.BOARD_TOP <= y < config.BOARD_TOP + config.BOARD_HEIGHT):
        return None

    clicked_col = x // config.SQ_SIZE
    clicked_row = (y - config.BOARD_TOP) // config.SQ_SIZE

    row = 7 - clicked_row if board_flipped else clicked_row
    col = 7 - clicked_col if board_flipped else clicked_col
    return row, col


def load_pieces_images(pieces_type: str = 'standard') -> None:
    """
    Initialize the global dictionary of images and load piece assets.

    Parameters
    ----------
    pieces_type : str, optional
        The subdirectory name for the image set to load. Default is 'standard'.

    Returns
    -------
    None
    """
    pieces = ['bB', 'bK', 'bN', 'bP', 'bQ', 'bR', 'wB', 'wK', 'wN', 'wP', 'wQ', 'wR']
    for piece in pieces:
        img_path = Path('pieces') / pieces_type / f'{piece}.png'
        if img_path.exists():
            config.IMAGES[piece] = pg.transform.smoothscale(
                pg.image.load(img_path),
                (config.SQ_SIZE, config.SQ_SIZE),
            )
        else:
            print(f"Image not found for piece: {piece}")

def cache_coordinate_fonts(coord_font: pg.font.Font) -> None:
    """
    Pre-render coordinate labels (1-8 and a-h) into surfaces to optimize performance.

    This function caches the surfaces for both 'white' and 'gray' square text colors.

    Parameters
    ----------
    coord_font : pygame.font.Font
        The font object used to render board coordinate labels.

    Returns
    -------
    None
    """
    for color_name, bg_color in zip(['white', 'grey'], config.board_colors):
        text_color = config.board_colors[1] if color_name == 'white' else config.board_colors[0]

        # Ranks 1-8
        for rank in range(1, 9):
            config.COORD_SURFACES[color_name][str(rank)] = coord_font.render(str(rank), True, text_color)

        # Files a-h
        for file in range(8):
            file_char = chr(ord('a') + file)
            config.COORD_SURFACES[color_name][file_char] = coord_font.render(file_char, True, text_color)

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
    gs : chess_engine.GameState
        The current state of the game containing board information.
    valid_moves : list of chess_engine.Move
        The list of currently valid moves for highlighting.
    sq_selected : tuple of int or None
        The currently selected square coordinates as (row, col), or None if
        no square is currently selected.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.
    coord_font : pygame.font.Font
        The font object used to render the board's coordinate labels.

    Returns
    -------
    None
    """
    draw_board(screen, coord_font, board_flipped)
    highlight_last_move(screen, gs, board_flipped)
    draw_pieces(screen, gs.board, board_flipped)
    highlight_current_square(screen, gs, valid_moves, sq_selected, board_flipped)


def draw_board(screen: pg.Surface, coord_font: pg.font.Font, board_flipped: bool) -> None:
    """
    Draw the checkered squares and the coordinates on the board.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    coord_font : pygame.font.Font
        The font object used to render the board's coordinate labels.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    for row in range(config.DIMENSION):
        for col in range(config.DIMENSION):
            color_index = (row + col) % 2
            color = config.board_colors[color_index]

            x, y = board_to_screen(row, col, board_flipped)
            rect = pg.Rect(x, y, config.SQ_SIZE, config.SQ_SIZE)
            pg.draw.rect(screen, color, rect)

            color_name = 'white' if color_index == 0 else 'grey'
            draw_row = (7 - row) if board_flipped else row
            draw_col = (7 - col) if board_flipped else col

            # Render Rank labels (1-8) on the left edge
            if draw_col == 0:
                rank_text = str(8 - row)
                text_surf = config.COORD_SURFACES[color_name][rank_text]
                screen.blit(text_surf, (rect.x + 2, rect.y + 2))

            # Render File labels (a-h) on the bottom edge
            if draw_row == 7:
                file_text = chr(ord('a') + col)
                text_surf = config.COORD_SURFACES[color_name][file_text]
                # Position it near the bottom right corner of the square
                text_x = rect.x + config.SQ_SIZE - text_surf.get_width() - 2
                text_y = rect.y + config.SQ_SIZE - text_surf.get_height() - 2
                screen.blit(text_surf, (text_x, text_y))


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

    Returns
    -------
    None
    """
    for row in range(config.DIMENSION):
        for col in range(config.DIMENSION):
            piece = board[row][col]
            if piece != '--':
                x, y = board_to_screen(row, col, board_flipped)
                screen.blit(config.IMAGES[piece], pg.Rect(x, y, config.SQ_SIZE, config.SQ_SIZE))

def highlight_current_square(
    screen: pg.Surface, gs: chess_engine.GameState, valid_moves: list[chess_engine.Move],
    sq_selected: tuple[int, int] | None, board_flipped: bool
) -> None:
    """
    Highlight the currently selected square and possible destinations.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    gs : chess_engine.GameState
        The current state of the game containing board information.
    valid_moves : list of chess_engine.Move
        The list of currently valid moves for highlighting.
    sq_selected : tuple of int or None
        The currently selected square coordinates as (row, col).
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    if sq_selected is not None:
        row, col = sq_selected
        if gs.board[row][col][0] == gs.friendly_color:
            current_sq = pg.Surface((config.SQ_SIZE, config.SQ_SIZE))
            current_sq.set_alpha(75)
            current_sq.fill(pg.Color('yellow'))

            screen.blit(current_sq, board_to_screen(row, col, board_flipped))

            for move in valid_moves:
                if move.start_row == row and move.start_col == col:
                    movable_indicator = pg.Surface((config.SQ_SIZE, config.SQ_SIZE), pg.SRCALPHA)
                    movable_indicator.fill((0, 0, 0, 0))

                    x_center, y_center = config.SQ_SIZE // 2, config.SQ_SIZE // 2
                    radius = config.SQ_SIZE // 6
                    transparent_green = (100, 180, 120, 175)
                    pg.draw.circle(movable_indicator, transparent_green, (x_center, y_center), radius)

                    screen.blit(movable_indicator, board_to_screen(move.end_row, move.end_col, board_flipped))


def highlight_last_move(screen: pg.Surface, gs: chess_engine.GameState, board_flipped: bool) -> None:
    """
    Highlight the starting and ending squares of the last executed move.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface to draw on.
    gs : chess_engine.GameState
        The current state of the game containing the move log history.
    board_flipped : bool
        Flag indicating whether the board perspective is currently flipped.

    Returns
    -------
    None
    """
    if len(gs.move_log) > 0:
        last_move = gs.move_log[-1]
        highlight_sq = pg.Surface((config.SQ_SIZE, config.SQ_SIZE))
        highlight_sq.set_alpha(100)
        highlight_sq.fill(pg.Color('yellow'))

        screen.blit(highlight_sq, board_to_screen(last_move.start_row, last_move.start_col, board_flipped))
        screen.blit(highlight_sq, board_to_screen(last_move.end_row, last_move.end_col, board_flipped))
