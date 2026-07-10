"""
Handles sliding piece animations.

This module provides visual feedback by animating chess pieces
moving smoothly from their start squares to their end squares.
"""
import pygame as pg
import chess_engine, config
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

    # Calculate actual visual positions on screen taking flip into account
    start_r_draw = (7 - anim_start_row) if board_flipped else anim_start_row
    start_c_draw = (7 - anim_start_col) if board_flipped else anim_start_col
    end_r_draw = (7 - anim_end_row) if board_flipped else anim_end_row
    end_c_draw = (7 - anim_end_col) if board_flipped else anim_end_col

    row_distance = end_r_draw - start_r_draw
    col_distance = end_c_draw - start_c_draw

    frames_per_square = 5
    frame_count = (abs(row_distance) + abs(col_distance)) * frames_per_square

    if frame_count == 0: return  # Prevent error

    for frame in range(frame_count + 1):
        row = start_r_draw + row_distance * frame / frame_count
        col = start_c_draw + col_distance * frame / frame_count

        # Redraw the underlying board and pieces
        graphics.draw_board(screen, coord_font, board_flipped)
        graphics.draw_pieces(screen, board, board_flipped)

        erase_r_draw = (7 - erase_row) if board_flipped else erase_row
        erase_c_draw = (7 - erase_col) if board_flipped else erase_col

        # Clear the square the piece is currently leaving
        color = config.board_colors[(erase_row + erase_col) % 2]
        erase_square = pg.Rect(erase_c_draw * config.SQ_SIZE, erase_r_draw * config.SQ_SIZE, config.SQ_SIZE, config.SQ_SIZE)
        pg.draw.rect(screen, color, erase_square)

        # Redraw captured piece if necessary to keep it visible until overwritten
        if not move_unmake and move.piece_captured != '--':
            if move.is_enpassant_move:
                ep_r_draw = (7 - move.start_row) if board_flipped else move.start_row
                ep_c_draw = (7 - move.end_col) if board_flipped else move.end_col
                enpassant_square = pg.Rect(ep_c_draw * config.SQ_SIZE, ep_r_draw * config.SQ_SIZE, config.SQ_SIZE, config.SQ_SIZE)
                screen.blit(config.IMAGES[move.piece_captured], enpassant_square)
            else:
                screen.blit(config.IMAGES[move.piece_captured], erase_square)

        # Draw the moving piece at its current interpolated position
        screen.blit(config.IMAGES[move.piece_moved], pg.Rect(col * config.SQ_SIZE, row * config.SQ_SIZE, config.SQ_SIZE, config.SQ_SIZE))

        pg.display.flip()
        clock.tick(config.ANIMATION_FPS)