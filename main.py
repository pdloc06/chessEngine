"""
Main driver for the chess program.

This module handles user input (mouse clicks and keyboard events) and
displays the current GameState object using pygame. It manages the game
loop, tying together graphics rendering, move animations, board flipping,
and pawn promotion logic.
"""
import pygame as pg
import chess_engine, move_finder, config
from gui import graphics, ui, animation


def main() -> None:
    """
    Initialize pygame, handle user input, and update graphics.

    This function sets up the main game loop, captures mouse and keyboard events
    to execute moves, undo moves, reset the game, handle pawn promotions,
    and continuously redraws the updated game state including the flipped board view.

    Returns
    -------
    None
    """
    pg.init()
    screen = pg.display.set_mode((config.WIDTH, config.HEIGHT))
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    move_log_font = pg.font.SysFont('Arial', 16, False, False)
    coord_font = pg.font.SysFont('Arial', 12, bold=True)  # Font for board coordinates

    gs = chess_engine.GameState()
    valid_moves = gs.get_valid_moves()

    # State flags for move execution and animation
    move_made = False
    move_unmake = False
    move_to_unmake = None  # Temp variable to store move to undo for animation purposes

    undone_moves = []  # Stack to manage forward and backward history
    board_flipped = False  # State flag for flipping the board view
    promoting_move = None  # Stores the move object temporarily when a pawn promotes

    # Pre-load assets
    graphics.load_pieces_images()
    graphics.cache_coordinate_fonts(coord_font)

    running = True
    game_over = False
    sq_selected = None
    player_clicks = []

    # State flags for player turns
    player_one_turn = True
    player_two_turn = False

    while running:
        human_turn = (gs.white_to_move and player_one_turn) or (not gs.white_to_move and player_two_turn)
        if not game_over and human_turn:
            for e in pg.event.get():
                if e.type == pg.QUIT:
                    running = False

                # Mouse Event Handling
                elif e.type == pg.MOUSEBUTTONDOWN:
                    if not game_over:
                        location = pg.mouse.get_pos()

                        # Intercept click if promotion interface is active
                        if promoting_move is not None:
                            menu_bg_rect, _, menu_rects = ui.get_promotion_menu_rects(promoting_move, board_flipped)
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
                        if location[0] < config.BOARD_WIDTH:
                            clicked_col = location[0] // config.SQ_SIZE
                            clicked_row = location[1] // config.SQ_SIZE

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
                                    if (move.start_row == player_clicks[0][0] and move.start_col == player_clicks[0][1] and
                                            move.end_row == player_clicks[1][0] and move.end_col == player_clicks[1][1]):

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

                        # Logic when a player clicks inside the move log panel area
                        else:
                            prev_btn, next_btn, restart_btn, flip_btn = ui.get_control_button_rects()

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
                                    gs.make_move(undone_moves.pop())
                                    move_made = True

                            else:
                                # Evaluate move selection clicks mathematically within the log
                                promoting_move = None
                                y_offset = location[1] - 5
                                if 0 < y_offset < config.BOARD_HEIGHT - 100:
                                    # Synchronize item_height with draw_move_log spacing
                                    item_height = move_log_font.get_height() + 6

                                    # Synchronize offset logic with the rendering function
                                    max_lines = (config.BOARD_HEIGHT - 120) // item_height
                                    start_line = max(0, ((len(gs.move_log) + 1) // 2) - max_lines)

                                    # Calculate target index including the scrolled offset
                                    target_index = (start_line + y_offset // item_height) * 2 + (
                                        0 if location[0] < config.BOARD_WIDTH + 120 else 1)

                                    if target_index < len(gs.move_log) + len(undone_moves):
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

                # Keyboard Event Handling
                elif e.type == pg.KEYDOWN:
                    if e.key == pg.K_z and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):  # CTRL/CMD + Z -> Undo move
                        promoting_move = None
                        if len(gs.move_log) != 0:
                            move_to_unmake = gs.move_log[-1]
                            undone_moves.append(move_to_unmake)
                            gs.unmake_move()
                            move_made = True
                            move_unmake = True

                    if e.key == pg.K_r and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):  # CTRL/CMD + R -> Reset game
                        gs = chess_engine.GameState()
                        valid_moves = gs.get_valid_moves()
                        move_made = False
                        move_unmake = False
                        move_to_unmake = None
                        sq_selected = None
                        player_clicks = []
                        promoting_move = None
                        undone_moves.clear()

        # Move finder move
        if not game_over and not human_turn:
            ai_move = move_finder.find_best_move(gs, gs.get_valid_moves(for_ai=True))
            gs.make_ai_move(ai_move)
            move_made = True
            undone_moves.clear()

        # Game State Updates & Animation
        if move_made:
            if move_unmake:
                animation.animate_move(
                    move_to_unmake, screen, gs.board, clock, board_flipped, coord_font, move_unmake=True
                )
            else:
                animation.animate_move(gs.move_log[-1], screen, gs.board, clock, board_flipped, coord_font)

            valid_moves = gs.get_valid_moves()

            # Identify absolute checkmate to format `#` in notation
            if gs.is_checkmate and len(gs.move_log) > 0:
                gs.move_log[-1].is_checkmate = True
                gs.move_log[-1].is_check = False

            # Reset flags
            move_made = False
            move_unmake = False
            move_to_unmake = None

        # Core Rendering
        graphics.draw_game_state(screen, gs, valid_moves, sq_selected, board_flipped, coord_font)
        ui.draw_move_log(screen, gs, move_log_font)

        # Draw promotion UI overlay if a pawn reached the end rank
        if promoting_move is not None:
            ui.draw_promotion_menu(screen, promoting_move, board_flipped, move_log_font)

        # Render match end states
        if gs.is_checkmate:
            game_over = True
            ui.winning_animation(screen, gs, not gs.white_to_move, board_flipped)
        elif gs.is_stalemate:
            game_over = True
            ui.stalemate_animation(screen, gs, board_flipped)

        clock.tick(config.MAX_FPS)
        pg.display.flip()


if __name__ == '__main__':
    main()