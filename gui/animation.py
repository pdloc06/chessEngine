"""
Handles sliding piece animations.

This module provides visual feedback by animating chess pieces
moving smoothly from their start squares to their end squares.
"""
import pygame as pg
import config
from engine import chess_engine
from gui import graphics


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
        The clock object utilized for framerate regulation.
    board_flipped : bool
        Flag indicating if the board is rendering from black's perspective.
    coord_font : pygame.font.Font
        The font for rendering coordinate text beneath the animation.
    move_unmake : bool, optional
        Flag signifying if the animation is reversing a previous move. Default is False.

    Returns
    -------
    None
    """
    # Reverse logic if the move is being undone
    if move_unmake:
        anim_start_row, anim_start_col = move.end_row, move.end_col
        anim_end_row, anim_end_col = move.start_row, move.start_col
        erase_row, erase_col = move.start_row, move.start_col
    else:
        anim_start_row, anim_start_col = move.start_row, move.start_col
        anim_end_row, anim_end_col = move.end_row, move.end_col
        erase_row, erase_col = move.end_row, move.end_col

    # Calculate actual pixel positions on screen (flip and bar offset included)
    start_x, start_y = graphics.board_to_screen(anim_start_row, anim_start_col, board_flipped)
    end_x, end_y = graphics.board_to_screen(anim_end_row, anim_end_col, board_flipped)

    row_distance = abs(anim_end_row - anim_start_row)
    col_distance = abs(anim_end_col - anim_start_col)

    frames_per_square = 5
    frame_count = (row_distance + col_distance) * frames_per_square

    if frame_count == 0: return  # Prevent error

    # The square being vacated never changes during the slide, so its
    # position and color are computed once, outside the frame loop
    erase_x, erase_y = graphics.board_to_screen(erase_row, erase_col, board_flipped)
    erase_square = pg.Rect(erase_x, erase_y, config.SQ_SIZE, config.SQ_SIZE)
    erase_color = config.board_colors[(erase_row + erase_col) % 2]

    for frame in range(frame_count + 1):
        x = start_x + (end_x - start_x) * frame / frame_count
        y = start_y + (end_y - start_y) * frame / frame_count

        # Redraw the underlying board and pieces
        graphics.draw_board(screen, coord_font, board_flipped)
        graphics.draw_pieces(screen, board, board_flipped)

        # Clear the square the piece is currently leaving
        pg.draw.rect(screen, erase_color, erase_square)

        # Redraw captured piece if necessary to keep it visible until overwritten
        if not move_unmake and move.piece_captured != '--':
            if move.is_enpassant_move:
                ep_x, ep_y = graphics.board_to_screen(move.start_row, move.end_col, board_flipped)
                enpassant_square = pg.Rect(ep_x, ep_y, config.SQ_SIZE, config.SQ_SIZE)
                screen.blit(config.IMAGES[move.piece_captured], enpassant_square)
            else:
                screen.blit(config.IMAGES[move.piece_captured], erase_square)

        # Draw the moving piece at its current interpolated position
        screen.blit(config.IMAGES[move.piece_moved], pg.Rect(x, y, config.SQ_SIZE, config.SQ_SIZE))

        pg.display.flip()
        clock.tick(config.ANIMATION_FPS)
