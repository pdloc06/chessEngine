"""
Main driver for the chess program.

This module handles user input (mouse clicks and keyboard events) and
displays the current GameState object using pygame. It manages the start
menu (choosing a Human or Computer opponent), the game loop, the turn
mechanism, graphics rendering, move animations, board flipping, and pawn
promotion logic.

Turn ownership rule: Player 1 always plays the color shown at the bottom of
the board. Flipping the board therefore also switches which color Player 1
(and, in AI mode, the computer) controls.
"""
import threading

import pygame as pg
import chess_engine, move_finder, config
from gui import graphics, ui, animation

# Type alias matching move_finder's lightweight move format
MoveTuple = tuple[int, int, int, int, int]


def run_main_menu(
    screen: pg.Surface,
    clock: pg.time.Clock,
    title_font: pg.font.Font,
    button_font: pg.font.Font
) -> bool | None:
    """
    Display the start menu and wait for the player to pick an opponent.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    clock : pygame.time.Clock
        The clock object for framerate regulation.
    title_font : pygame.font.Font
        Large font used for the game title.
    button_font : pygame.font.Font
        Font used for the menu buttons.

    Returns
    -------
    bool or None
        True to play against the computer, False for two human players,
        or None if the window was closed.
    """
    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                return None
            if e.type == pg.MOUSEBUTTONDOWN:
                vs_ai_btn, two_players_btn = ui.get_menu_button_rects()
                if vs_ai_btn.collidepoint(e.pos):
                    return True
                if two_players_btn.collidepoint(e.pos):
                    return False

        ui.draw_main_menu(screen, title_font, button_font, pg.mouse.get_pos())
        pg.display.flip()
        clock.tick(config.MAX_FPS)


def start_ai_search(gs: chess_engine.GameState, generation: int, holder: dict) -> None:
    """
    Launch the AI move search on a background daemon thread.

    The search runs on an isolated copy of the game state (rebuilt from FEN
    plus the Zobrist history for repetition awareness), so the UI thread can
    keep rendering the real board while the engine thinks.

    Parameters
    ----------
    gs : chess_engine.GameState
        The live game state to search from (not mutated).
    generation : int
        Tag identifying this search; stale results (from searches invalidated
        by an undo, flip, or restart) are recognized by a mismatched tag.
    holder : dict
        Shared result slot. The worker writes 'move' first, then 'generation',
        so a matching generation guarantees the move is present.

    Returns
    -------
    None
    """
    search_gs = chess_engine.GameState.from_fen(gs.to_fen())
    # Carry the real game's position history over so the engine can detect
    # (or aim for) threefold repetitions correctly
    search_gs.zobrist_history = list(gs.zobrist_history)

    def _worker() -> None:
        best: MoveTuple | None = move_finder.find_best_move(
            search_gs,
            max_depth=config.AI_MAX_DEPTH,
            time_limit=config.AI_TIME_LIMIT,
        )
        holder['move'] = best
        holder['generation'] = generation

    threading.Thread(target=_worker, daemon=True).start()


def run_game(
    screen: pg.Surface,
    clock: pg.time.Clock,
    move_log_font: pg.font.Font,
    coord_font: pg.font.Font,
    bar_font: pg.font.Font,
    vs_ai: bool
) -> None:
    """
    Run the main game loop: input handling, turn management, and rendering.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    clock : pygame.time.Clock
        The clock object for framerate regulation.
    move_log_font : pygame.font.Font
        Font for the move log panel and promotion menu.
    coord_font : pygame.font.Font
        Font for the board coordinate labels.
    bar_font : pygame.font.Font
        Font for the player info bars.
    vs_ai : bool
        True when Player 2 is the AI move finder.

    Returns
    -------
    None
    """
    gs = chess_engine.GameState()
    valid_moves = gs.get_valid_moves()

    # State flags for move execution and animation
    move_made = False
    move_unmake = False
    move_to_unmake = None  # Temp variable to store move to undo for animation purposes

    undone_moves: list[chess_engine.Move] = []  # Stack to manage forward and backward history
    board_flipped = False  # State flag for flipping the board view
    promoting_move: chess_engine.Move | None = None  # Stores the move object temporarily when a pawn promotes

    running = True
    game_over = False
    sq_selected: tuple[int, int] | None = None
    player_clicks: list[tuple[int, int]] = []

    # AI search state: results arrive asynchronously tagged with a generation
    # counter, so anything started before an undo/flip/restart gets discarded
    ai_thinking = False
    search_generation = 0
    ai_result: dict = {}

    def invalidate_ai_search() -> None:
        """Discard any in-flight AI search result (after undo/flip/restart)."""
        nonlocal ai_thinking, search_generation
        search_generation += 1
        ai_thinking = False

    def is_human_turn() -> bool:
        """Check whether the side to move is controlled by a human."""
        player_one_color = 'b' if board_flipped else 'w'
        return (not vs_ai) or gs.friendly_color == player_one_color

    def undo_half_moves(count: int) -> int:
        """Undo up to `count` half-moves, pushing them onto the redo stack."""
        undone = 0
        for _ in range(count):
            if not gs.move_log:
                break
            undone_moves.append(gs.move_log[-1])
            gs.unmake_move()
            undone += 1
        return undone

    def undo_for_player() -> None:
        """
        Undo one half-move; in AI mode keep undoing (max one more) until it
        is the human's turn again, so the AI doesn't instantly replay.
        """
        nonlocal move_made, move_unmake, move_to_unmake
        invalidate_ai_search()
        if undo_half_moves(1):
            if vs_ai and gs.move_log and not is_human_turn():
                undo_half_moves(1)
            move_made = True
            move_unmake = True
            move_to_unmake = undone_moves[-1]

    def reset_game() -> None:
        """Restore a fresh GameState and clear every interaction flag."""
        nonlocal gs, valid_moves, sq_selected, player_clicks
        nonlocal move_made, move_unmake, move_to_unmake, promoting_move, game_over
        invalidate_ai_search()
        gs = chess_engine.GameState()
        valid_moves = gs.get_valid_moves()
        sq_selected = None
        player_clicks = []
        move_made = False
        move_unmake = False
        move_to_unmake = None
        promoting_move = None
        game_over = False
        undone_moves.clear()

    while running:
        human_turn = is_human_turn()

        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False

            # Mouse Event Handling (panel buttons stay usable after game over,
            # so the player can restart or step back through the history)
            elif e.type == pg.MOUSEBUTTONDOWN:
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

                # Logic when a player clicks inside the board area
                elif location[0] < config.BOARD_WIDTH:
                    clicked_square = graphics.screen_to_board(location[0], location[1], board_flipped)

                    # Ignore board clicks on the player bars, after the game
                    # has ended, or during the AI's turn
                    if clicked_square is None or game_over or not human_turn or ai_thinking:
                        continue

                    if sq_selected == clicked_square:
                        sq_selected = None
                        player_clicks = []
                    else:
                        sq_selected = clicked_square
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
                            player_clicks = player_clicks[1:]

                # Logic when a player clicks inside the move log panel area
                else:
                    prev_btn, next_btn, restart_btn, flip_btn = ui.get_control_button_rects()

                    if restart_btn.collidepoint(location):
                        reset_game()

                    elif flip_btn.collidepoint(location):
                        # Flipping also swaps which color Player 1 controls,
                        # so any in-flight AI search must be discarded
                        board_flipped = not board_flipped
                        invalidate_ai_search()
                        sq_selected = None
                        player_clicks = []

                    elif prev_btn.collidepoint(location):
                        promoting_move = None
                        undo_for_player()

                    elif next_btn.collidepoint(location):
                        promoting_move = None
                        invalidate_ai_search()
                        if len(undone_moves) > 0:
                            gs.make_move(undone_moves.pop())
                            move_made = True

                    else:
                        # Evaluate move selection clicks mathematically within the log
                        promoting_move = None
                        target_index = ui.get_move_log_click_index(location, len(gs.move_log), move_log_font)

                        if target_index is not None and target_index < len(gs.move_log) + len(undone_moves):
                            target_len = target_index + 1
                            current_len = len(gs.move_log)

                            # Undo iteratively to reach history target
                            if target_len < current_len:
                                invalidate_ai_search()
                                undo_half_moves(current_len - target_len)
                                move_made = True
                                move_unmake = True
                                move_to_unmake = undone_moves[-1]

                            # Redo iteratively to reach history target
                            elif target_len > current_len:
                                invalidate_ai_search()
                                for _ in range(target_len - current_len):
                                    gs.make_move(undone_moves.pop())
                                move_made = True

            # Keyboard Event Handling
            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_z and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):  # CTRL/CMD + Z -> Undo move
                    promoting_move = None
                    if len(gs.move_log) != 0:
                        undo_for_player()

                if e.key == pg.K_r and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):  # CTRL/CMD + R -> Reset game
                    reset_game()

        # AI turn: launch a background search, then collect its result
        if not game_over and not move_made and not is_human_turn() and promoting_move is None:
            if not ai_thinking:
                ai_thinking = True
                start_ai_search(gs, search_generation, ai_result)
            elif ai_result.get('generation') == search_generation:
                ai_move_tuple: MoveTuple | None = ai_result.get('move')
                ai_result.clear()
                ai_thinking = False

                if ai_move_tuple is not None:
                    ai_move = chess_engine.Move.from_ai_tuple(ai_move_tuple, gs.board)
                    # Reuse the pre-generated Move so notation metadata
                    # (disambiguation) stays intact in the move log
                    matched = next((m for m in valid_moves if m == ai_move), ai_move)
                    gs.make_move(matched)
                    move_made = True
                    undone_moves.clear()

        # Game State Updates & Animation
        if move_made:
            if move_unmake and move_to_unmake is not None:
                animation.animate_move(
                    move_to_unmake, screen, gs.board, clock, board_flipped, coord_font, move_unmake=True
                )
            elif len(gs.move_log) > 0:
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
            game_over = False

        # Core Rendering
        graphics.draw_game_state(screen, gs, valid_moves, sq_selected, board_flipped, coord_font)
        ui.draw_player_bars(screen, gs, bar_font, board_flipped, vs_ai, ai_thinking)
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


def main() -> None:
    """
    Initialize pygame, show the opponent menu, and enter the game loop.

    Returns
    -------
    None
    """
    pg.init()
    screen = pg.display.set_mode((config.WIDTH, config.HEIGHT))
    pg.display.set_caption('PyCheckmate')
    clock = pg.time.Clock()
    screen.fill(pg.Color('white'))

    move_log_font = pg.font.SysFont('Arial', 16, False, False)
    coord_font = pg.font.SysFont('Arial', 12, bold=True)  # Font for board coordinates
    title_font = pg.font.SysFont('Arial', 48, bold=True)
    bar_font = pg.font.SysFont('Arial', 16, bold=True)

    # Pre-load assets
    graphics.load_pieces_images()
    graphics.cache_coordinate_fonts(coord_font)

    vs_ai = run_main_menu(screen, clock, title_font, move_log_font)
    if vs_ai is not None:
        run_game(screen, clock, move_log_font, coord_font, bar_font, vs_ai)

    pg.quit()


if __name__ == '__main__':
    main()
