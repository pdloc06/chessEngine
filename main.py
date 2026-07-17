"""
Main driver for the chess program.

This module handles user input (mouse clicks and keyboard events) and
displays the current GameState object using pygame. It manages the start
menu (choosing a Human or Computer opponent, then a time control), the game
loop, the turn mechanism, the per-player chess clocks, graphics rendering,
move animations, board flipping, and pawn promotion logic.

Turn ownership rule: Player 1 always plays the color shown at the bottom of
the board. Flipping the board therefore also switches which color Player 1
(and, in AI mode, the computer) controls.
"""
import threading

import pygame as pg
import config
from engine import analysis, chess_engine, move_finder, pgn, uci_client
from gui import graphics, ui, animation, review

# Type alias matching move_finder's lightweight move format
MoveTuple = tuple[int, int, int, int, int]

# Shared UCI engine subprocess (PyPy-hosted when available, see uci_client).
# Created lazily on the first AI turn and reused for the whole session so
# PyPy's JIT stays warm between moves. The 'failed' latch makes sure a
# broken setup is only attempted once, not on every single AI turn.
_engine_client: uci_client.UciEngineClient | None = None
_engine_client_failed = False


def _get_engine_client() -> uci_client.UciEngineClient | None:
    """
    Return the shared UCI engine subprocess, spawning it on first use.

    Returns
    -------
    uci_client.UciEngineClient | None
        A ready engine client, or None when disabled by config, no PyPy
        interpreter exists, or a previous attempt failed (in-process
        search is the fallback in every case).
    """
    global _engine_client, _engine_client_failed
    if not config.AI_USE_UCI_ENGINE or _engine_client_failed:
        return None
    if _engine_client is None:
        command = uci_client.resolve_engine_command()
        if command is None:
            _engine_client_failed = True
            return None
        try:
            _engine_client = uci_client.UciEngineClient(command)
        except uci_client.EngineClientError:
            _engine_client_failed = True
            return None
    return _engine_client


def _drop_engine_client() -> None:
    """Shut down a misbehaving engine subprocess and stop using it."""
    global _engine_client, _engine_client_failed
    if _engine_client is not None:
        _engine_client.close()
        _engine_client = None
    _engine_client_failed = True


def run_main_menu(
    screen: pg.Surface,
    clock: pg.time.Clock,
    title_font: pg.font.Font,
    button_font: pg.font.Font
) -> str | None:
    """
    Display the start menu and wait for the player to pick a mode.

    The menu also hosts the piece-set selector, which cycles through the
    image sets found under pieces/ and reloads the piece graphics in place.

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
    str or None
        'ai' to play against the computer, 'human' for two players,
        'analysis' for the FEN/PGN game-analysis flow, or None if the
        window was closed.
    """
    piece_sets = graphics.list_piece_sets()
    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                return None
            if e.type == pg.MOUSEBUTTONDOWN:
                vs_ai_btn, two_players_btn, analysis_btn = ui.get_menu_button_rects()
                if vs_ai_btn.collidepoint(e.pos):
                    return 'ai'
                if two_players_btn.collidepoint(e.pos):
                    return 'human'
                if analysis_btn.collidepoint(e.pos):
                    return 'analysis'
                if ui.get_piece_set_button_rect().collidepoint(e.pos):
                    # Cycle to the next set and reload the piece images so
                    # the preview (and the next game) picks them up
                    current = piece_sets.index(config.PIECE_SET) if config.PIECE_SET in piece_sets else 0
                    next_set = piece_sets[(current + 1) % len(piece_sets)]
                    graphics.load_pieces_images(next_set)

        ui.draw_main_menu(screen, title_font, button_font, pg.mouse.get_pos())
        pg.display.flip()
        clock.tick(config.MAX_FPS)


def run_time_control_menu(
    screen: pg.Surface,
    clock: pg.time.Clock,
    title_font: pg.font.Font,
    button_font: pg.font.Font
) -> str | None:
    """
    Display the time-control menu and wait for the player to pick a mode.

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
    str or None
        The chosen key into `config.GAME_MODES`, `ui.BACK_SENTINEL` if the
        player asked to return to the opponent menu, or None if the window
        was closed.
    """
    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                return None
            if e.type == pg.MOUSEBUTTONDOWN:
                if ui.get_back_button_rect().collidepoint(e.pos):
                    return ui.BACK_SENTINEL
                for rect, mode in ui.get_time_control_button_rects():
                    if rect.collidepoint(e.pos):
                        return mode

        ui.draw_time_control_menu(screen, title_font, button_font, pg.mouse.get_pos())
        pg.display.flip()
        clock.tick(config.MAX_FPS)


# Game-long transposition table for the in-process fallback search, so each
# move starts warm from the previous searches' work (the same reuse the PyPy
# UCI subprocess already gets on its own side). Reset by invalidate_ai_search()
# on undo/flip/restart. Unused when the UCI subprocess handles the search.
_ai_transposition_table: move_finder.TTable = {}


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

    # Snapshot the game's move list now, on the UI thread: the engine
    # subprocess replays it from the start position, which both sets up the
    # position and rebuilds the repetition history on the engine's side
    uci_moves = [move.get_uci_notation() for move in gs.move_log]

    def _worker() -> None:
        best: MoveTuple | None = None

        # Preferred path: the persistent UCI subprocess (PyPy-hosted when
        # available). Any protocol failure just drops us to the fallback.
        client = _get_engine_client()
        if client is not None:
            try:
                best_uci = client.search_from_moves(
                    uci_moves, config.AI_MAX_DEPTH, config.AI_TIME_LIMIT
                )
                best = next(
                    (
                        move for move in search_gs.get_valid_moves(for_ai=True)
                        if chess_engine.Move.from_ai_tuple(move, search_gs.board)
                        .get_uci_notation() == best_uci
                    ),
                    None,
                )
            except uci_client.EngineClientError:
                _drop_engine_client()

        # Fallback (and the normal path without PyPy): search in-process
        if best is None:
            best = move_finder.find_best_move(
                search_gs,
                max_depth=config.AI_MAX_DEPTH,
                time_limit=config.AI_TIME_LIMIT,
                tt=_ai_transposition_table,
            )
        holder['move'] = best
        holder['generation'] = generation

    threading.Thread(target=_worker, daemon=True).start()


def _parse_imported_game(text: str) -> chess_engine.GameState:
    """
    Turn the import screen's text buffer into a replayed GameState.

    Parameters
    ----------
    text : str
        The user-supplied string; a FEN (single line, 8 ranks) is loaded as
        a bare position, anything else is parsed as PGN movetext.

    Returns
    -------
    chess_engine.GameState
        The imported game, with any moves already applied.

    Raises
    ------
    ValueError
        With a user-readable message when the text is not a valid game.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError('Paste a FEN or a PGN first')

    if pgn.looks_like_fen(stripped):
        placement = stripped.split()[0]
        if placement.count('K') != 1 or placement.count('k') != 1:
            raise ValueError('A FEN needs exactly one king per side')
        gs = chess_engine.GameState.from_fen(stripped)
        gs.get_valid_moves()  # Prime the check/terminal flags for rendering
        return gs

    return pgn.game_from_pgn(stripped)


def _clone_game_for_review(gs: chess_engine.GameState) -> chess_engine.GameState:
    """
    Rebuild the current game on a fresh GameState for reviewing.

    The review navigates (and can even extend) its GameState freely, so it
    must never share state with the live game. Replaying through the move
    generator keeps notation metadata (disambiguation, check marks) intact.

    Parameters
    ----------
    gs : chess_engine.GameState
        The live game (games always start from the standard position).

    Returns
    -------
    chess_engine.GameState
        An independent copy holding the same move history.
    """
    clone = chess_engine.GameState()
    for played in gs.move_log:
        target = played.to_ai_tuple()
        matched = next(
            move for move in clone.get_valid_moves()
            if move.to_ai_tuple() == target
        )
        clone.make_move(matched)

    clone.get_valid_moves()  # Refresh terminal flags
    if clone.is_checkmate and clone.move_log:
        clone.move_log[-1].is_checkmate = True
        clone.move_log[-1].is_check = False
    return clone


def run_import_menu(
    screen: pg.Surface,
    clock: pg.time.Clock,
    title_font: pg.font.Font,
    font: pg.font.Font,
) -> chess_engine.GameState | str:
    """
    Display the Game Analysis import screen and collect a FEN or PGN.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface.
    clock : pygame.time.Clock
        The clock object for framerate regulation.
    title_font : pygame.font.Font
        Large font used for the screen title.
    font : pygame.font.Font
        Font for the text area and buttons.

    Returns
    -------
    chess_engine.GameState or str
        The successfully imported game, or the sentinel strings 'back'
        (return to the main menu) / 'quit' (window closed).
    """
    text = ''
    error: str | None = None

    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                return 'quit'

            if e.type == pg.MOUSEBUTTONDOWN:
                back_btn, paste_btn, clear_btn, analyze_btn = review.get_import_button_rects()
                if back_btn.collidepoint(e.pos):
                    return 'back'
                if paste_btn.collidepoint(e.pos):
                    text += review.get_clipboard_text()
                    error = None
                elif clear_btn.collidepoint(e.pos):
                    text, error = '', None
                elif analyze_btn.collidepoint(e.pos):
                    try:
                        return _parse_imported_game(text)
                    except ValueError as exc:
                        error = str(exc)
                    except Exception:
                        error = 'Could not read that as a FEN or PGN'

            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_v and (e.mod & (pg.KMOD_META | pg.KMOD_CTRL)):
                    text += review.get_clipboard_text()
                    error = None
                elif e.key == pg.K_BACKSPACE:
                    text = text[:-1]
                elif e.key == pg.K_RETURN:
                    text += '\n'
                elif e.unicode and e.unicode.isprintable():
                    text += e.unicode

        review.draw_import_menu(screen, title_font, font, text, error, pg.mouse.get_pos())
        pg.display.flip()
        clock.tick(config.MAX_FPS)


def run_review(
    screen: pg.Surface,
    clock: pg.time.Clock,
    move_log_font: pg.font.Font,
    coord_font: pg.font.Font,
    bar_font: pg.font.Font,
    gs: chess_engine.GameState,
) -> str | None:
    """
    Run the game-review screen for an imported or just-played game.

    The board gains an evaluation-bar gutter on the left (the window is
    temporarily widened for it), every move gets a chess.com-style quality
    tag as the background analysis progresses, and the current move's tag is
    also badged on the piece it moved. New moves can be played directly on
    the board from any position: at the game's end they extend the game,
    anywhere else they branch into a variation with its own analysis, left
    again via the "Back to game" strip or by rewinding past its start.

    Parameters
    ----------
    screen : pygame.Surface
        The main display surface (recreated wider while reviewing).
    clock : pygame.time.Clock
        The clock object for framerate regulation.
    move_log_font : pygame.font.Font
        Font for the review panel.
    coord_font : pygame.font.Font
        Font for the board coordinate labels.
    bar_font : pygame.font.Font
        Font for the player bars.
    gs : chess_engine.GameState
        The game to review. Must be a dedicated copy: the review rewinds and
        replays it, and can append exploration moves.

    Returns
    -------
    str or None
        'exit' when the player leaves the review normally, or None if the
        window was closed (the caller should shut the program down).
    """
    # Enter the review layout: widen the window and shift the board right so
    # the eval bar gets its gutter (board_to_screen handles the rest)
    config.BOARD_LEFT = config.EVAL_BAR_WIDTH
    screen = pg.display.set_mode((config.EVAL_BAR_WIDTH + config.WIDTH, config.HEIGHT))
    eval_font = pg.font.SysFont('Arial', 11, bold=True)

    # Rewind to the first position; its FEN seeds the background analyser
    all_moves: list[chess_engine.Move] = list(gs.move_log)
    for _ in range(len(all_moves)):
        gs.unmake_move()
    start_fen = gs.to_fen()
    cursor = 0

    worker = analysis.GameAnalysis(
        start_fen,
        [move.to_ai_tuple() for move in all_moves],
        config.REVIEW_MAX_DEPTH,
        config.REVIEW_TIME_LIMIT,
    )

    board_flipped = False
    valid_moves = gs.get_valid_moves()
    sq_selected: tuple[int, int] | None = None
    player_clicks: list[tuple[int, int]] = []
    displayed_share = 0.5  # Smoothed eval-bar position (chess.com-style glide)
    result: str | None = None

    # Variation state: playing a move that departs from the game branches
    # into a side line with its own analyser. Rewinding past its first move
    # (or the "Back to game" strip) returns to the mainline, whose analysis
    # keeps running in the background the whole time.
    var_fen = ''
    var_base = 0
    var_moves: list[chess_engine.Move] = []
    var_worker: analysis.GameAnalysis | None = None
    var_cursor = 0

    def current_variation() -> review.Variation | None:
        """Package the variation state for the drawing/hit-test helpers."""
        if var_worker is None:
            return None
        return var_base, var_moves, var_worker, var_cursor

    def refresh_position() -> None:
        """Regenerate legal moves and clear the click selection after a nav."""
        nonlocal valid_moves, sq_selected, player_clicks
        valid_moves = gs.get_valid_moves()
        sq_selected = None
        player_clicks = []

    def mark_terminal(move: chess_engine.Move) -> None:
        """Stamp '#' on a just-played mating move (flags are already fresh)."""
        if gs.is_checkmate:
            move.is_checkmate = True
            move.is_check = False

    def leave_variation(unwind: bool) -> None:
        """
        Drop the active variation and return to the mainline.

        With `unwind`, the variation's moves are also taken back off the
        board (used by the Back strip and mainline jumps); without it the
        caller has already navigated to the branch point.
        """
        nonlocal var_worker, var_moves, var_cursor
        if var_worker is None:
            return
        if unwind:
            for _ in range(var_cursor):
                gs.unmake_move()
        var_worker.stop()
        var_worker = None
        var_moves = []
        var_cursor = 0
        refresh_position()

    def go_to(target: int, animate: bool = False) -> None:
        """Jump to `target` mainline moves played (exits any variation)."""
        nonlocal cursor
        leave_variation(unwind=True)
        target = max(0, min(target, len(all_moves)))
        if target == cursor:
            return
        step_move = None
        unmaking = target < cursor
        while cursor < target:
            gs.make_move(all_moves[cursor])
            cursor += 1
            step_move = gs.move_log[-1]
        while cursor > target:
            step_move = gs.move_log[-1]
            gs.unmake_move()
            cursor -= 1
        if animate and step_move is not None:
            animation.animate_move(
                step_move, screen, gs.board, clock, board_flipped, coord_font,
                move_unmake=unmaking
            )
        refresh_position()

    def go_to_in_variation(target: int) -> None:
        """Jump to `target` variation moves played (0 exits the variation)."""
        nonlocal var_cursor
        target = max(0, min(target, len(var_moves)))
        while var_cursor > target:
            gs.unmake_move()
            var_cursor -= 1
        while var_cursor < target:
            gs.make_move(var_moves[var_cursor])
            var_cursor += 1
        if var_cursor == 0:
            leave_variation(unwind=False)
        else:
            refresh_position()

    def step_forward() -> None:
        """Advance one move along the active line, with animation."""
        nonlocal var_cursor
        if var_worker is not None:
            if var_cursor < len(var_moves):
                move = var_moves[var_cursor]
                gs.make_move(move)
                var_cursor += 1
                animation.animate_move(move, screen, gs.board, clock, board_flipped, coord_font)
                refresh_position()
        else:
            go_to(cursor + 1, animate=True)

    def step_back() -> None:
        """Step one move back; leaving a variation's first move exits it."""
        nonlocal var_cursor
        if var_worker is not None:
            move = gs.move_log[-1]
            gs.unmake_move()
            var_cursor -= 1
            animation.animate_move(
                move, screen, gs.board, clock, board_flipped, coord_font, move_unmake=True
            )
            if var_cursor == 0:
                leave_variation(unwind=False)
            else:
                refresh_position()
        else:
            go_to(cursor - 1, animate=True)

    def play_board_move(matched: chess_engine.Move) -> None:
        """
        Handle a move the user played on the board, branching if needed.

        Following the current line just steps forward. A new move at the
        game's end extends the mainline analysis; anywhere else it opens
        (or extends/rewrites) a variation with its own analyser.
        """
        nonlocal cursor, var_fen, var_base, var_moves, var_worker, var_cursor

        if var_worker is None:
            if cursor < len(all_moves) and matched == all_moves[cursor]:
                go_to(cursor + 1, animate=True)  # Just following the game
                return
            if cursor == len(all_moves):
                # Past the final position: the game itself grows
                gs.make_move(matched)
                all_moves.append(matched)
                worker.append_move(matched.to_ai_tuple())
                cursor += 1
                animation.animate_move(matched, screen, gs.board, clock, board_flipped, coord_font)
                refresh_position()
                mark_terminal(matched)
                return
            # Departing from the game mid-history: open a variation
            var_fen = gs.to_fen()
            var_base = cursor
            gs.make_move(matched)
            var_moves = [matched]
            var_worker = analysis.GameAnalysis(
                var_fen, [matched.to_ai_tuple()],
                config.REVIEW_MAX_DEPTH, config.REVIEW_TIME_LIMIT,
            )
            var_cursor = 1
            animation.animate_move(matched, screen, gs.board, clock, board_flipped, coord_font)
            refresh_position()
            mark_terminal(matched)
            return

        if var_cursor < len(var_moves) and matched == var_moves[var_cursor]:
            step_forward()  # Just following the variation
            return

        gs.make_move(matched)
        if var_cursor == len(var_moves):
            # At the variation's tip: extend it
            var_moves.append(matched)
            var_worker.append_move(matched.to_ai_tuple())
        else:
            # Mid-variation: keep the prefix, replace the tail. The analyser
            # cannot truncate, so the shortened line gets a fresh one.
            var_worker.stop()
            var_moves = var_moves[:var_cursor] + [matched]
            var_worker = analysis.GameAnalysis(
                var_fen, [move.to_ai_tuple() for move in var_moves],
                config.REVIEW_MAX_DEPTH, config.REVIEW_TIME_LIMIT,
            )
        var_cursor += 1
        animation.animate_move(matched, screen, gs.board, clock, board_flipped, coord_font)
        refresh_position()
        mark_terminal(matched)

    running = True
    while running:
        dt_ms = clock.tick(config.MAX_FPS)

        for e in pg.event.get():
            if e.type == pg.QUIT:
                running = False
                result = None

            elif e.type == pg.MOUSEBUTTONDOWN:
                location = e.pos
                exit_btn, prev_btn, next_btn, flip_btn = review.get_review_button_rects()

                if exit_btn.collidepoint(location):
                    running = False
                    result = 'exit'
                elif prev_btn.collidepoint(location):
                    step_back()
                elif next_btn.collidepoint(location):
                    step_forward()
                elif flip_btn.collidepoint(location):
                    board_flipped = not board_flipped
                    sq_selected = None
                    player_clicks = []

                elif location[0] >= review.panel_x():
                    if var_worker is not None and review.get_variation_back_rect().collidepoint(location):
                        leave_variation(unwind=True)
                    else:
                        # The list shows the variation while one is active,
                        # so the returned index belongs to the active line
                        target_index = review.get_review_log_click_index(
                            location, len(all_moves), cursor, move_log_font,
                            current_variation()
                        )
                        if target_index is not None:
                            if var_worker is not None:
                                go_to_in_variation(target_index + 1)
                            else:
                                go_to(target_index + 1)

                else:
                    clicked_square = graphics.screen_to_board(location[0], location[1], board_flipped)
                    if clicked_square is None:
                        continue
                    # Move input mirrors the game's two-click entry (with
                    # auto-queen promotions); any legal move works from any
                    # position — departures from the game open a variation
                    if sq_selected == clicked_square:
                        sq_selected = None
                        player_clicks = []
                    else:
                        sq_selected = clicked_square
                        player_clicks.append(sq_selected)
                    if len(player_clicks) == 2:
                        matched = next(
                            (
                                move for move in valid_moves
                                if (move.start_row, move.start_col) == player_clicks[0]
                                and (move.end_row, move.end_col) == player_clicks[1]
                            ),
                            None,
                        )
                        if matched is not None:
                            play_board_move(matched)
                        else:
                            player_clicks = player_clicks[1:]

            elif e.type == pg.KEYDOWN:
                if e.key == pg.K_LEFT:
                    step_back()
                elif e.key == pg.K_RIGHT:
                    step_forward()
                elif e.key == pg.K_UP:
                    go_to(0)
                elif e.key == pg.K_DOWN:
                    go_to(len(all_moves))

        # Eval bar: glide the displayed share toward the current position's
        # win percentage as its evaluation becomes available. The active
        # line (mainline or variation) supplies the evaluation and tags.
        if var_worker is not None:
            current_eval = var_worker.evals[var_cursor]
            shown_move = var_moves[var_cursor - 1] if var_cursor > 0 else None
            shown_tag = var_worker.tags[var_cursor - 1] if var_cursor > 0 else None
        else:
            current_eval = worker.evals[cursor]
            shown_move = all_moves[cursor - 1] if cursor > 0 else None
            shown_tag = worker.tags[cursor - 1] if cursor > 0 else None
        if current_eval is not None:
            target_share = analysis.win_percent(current_eval.score_white) / 100.0
            eval_text = analysis.format_eval(current_eval.score_white)
        else:
            target_share = displayed_share
            eval_text = '...'
        displayed_share += (target_share - displayed_share) * min(1.0, dt_ms * 0.01)

        # Rendering
        graphics.draw_game_state(screen, gs, valid_moves, sq_selected, board_flipped, coord_font)
        review.draw_review_bars(screen, gs, bar_font, board_flipped)
        review.draw_eval_bar(screen, displayed_share, eval_text, board_flipped, eval_font)
        review.draw_review_panel(
            screen, all_moves, worker, cursor, move_log_font, current_variation()
        )

        if shown_move is not None and shown_tag is not None:
            review.draw_move_badge(screen, shown_move, shown_tag, board_flipped)
        if current_eval is not None and current_eval.best_move is not None:
            review.draw_best_move_arrow(screen, current_eval.best_move, board_flipped)
        if gs.is_checkmate or gs.is_stalemate:
            # Only a line's final position can be terminal, so the flags
            # (refreshed on every navigation) are safe to trust here
            review.draw_end_badges(screen, gs, board_flipped)

        pg.display.flip()

    # Leave the review layout: stop the analysers and restore the window
    worker.stop()
    if var_worker is not None:
        var_worker.stop()
    config.BOARD_LEFT = 0
    pg.display.set_mode((config.WIDTH, config.HEIGHT))
    return result


def run_game(
    screen: pg.Surface,
    clock: pg.time.Clock,
    move_log_font: pg.font.Font,
    coord_font: pg.font.Font,
    bar_font: pg.font.Font,
    vs_ai: bool,
    mode_key: str
) -> bool:
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
    mode_key : str
        Key into `config.GAME_MODES` picking the time control (or "No Clock"
        to play untimed).

    Returns
    -------
    bool
        True if the player left via the in-game "Main Menu" button (the
        caller should loop back to the opponent menu); False if the window
        was closed instead.
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
    return_to_menu = False  # Set when the player clicks "Main Menu" instead of closing the window

    running = True
    game_over = False
    sq_selected: tuple[int, int] | None = None
    player_clicks: list[tuple[int, int]] = []

    # Chess clock: None means untimed (mirrors the pre-timer behavior). The
    # increment is added to the mover's own clock right after their move.
    initial_time, increment = config.GAME_MODES[mode_key]
    clocks: dict[str, float] | None = (
        {'w': float(initial_time), 'b': float(initial_time)} if initial_time is not None else None
    )
    flag_fallen: str | None = None  # Color whose clock reached zero, if any

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
        # The board changed under the fallback engine; drop its warm table so a
        # stale line can't leak into the next search.
        _ai_transposition_table.clear()

    def is_human_turn() -> bool:
        """Check whether the side to move is controlled by a human."""
        player_one_color = 'b' if board_flipped else 'w'
        return (not vs_ai) or gs.friendly_color == player_one_color

    def apply_increment(mover_color: str) -> None:
        """Credit the increment to the player who just completed a live move."""
        if clocks is not None:
            clocks[mover_color] += increment

    def commit_move(move: chess_engine.Move) -> None:
        """Play a live move: apply it, credit the increment, clear the redo stack."""
        nonlocal move_made
        mover_color = gs.friendly_color
        gs.make_move(move)
        apply_increment(mover_color)
        move_made = True
        undone_moves.clear()

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
        nonlocal clocks, flag_fallen
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
        clocks = {'w': float(initial_time), 'b': float(initial_time)} if initial_time is not None else None
        flag_fallen = None

    while running:
        # Ticking is measured once per frame here (rather than at the loop's
        # end) so the elapsed time is known before anything else this frame
        # reads the clocks
        dt_ms = clock.tick(config.MAX_FPS)
        human_turn = is_human_turn()

        # Chess-clock countdown: only the side to move loses time, and only
        # while a game is actually in progress (not mid-promotion or over).
        # Time spent inside animate_move's own render loop isn't charged
        # here since it ticks the same pygame Clock itself.
        if clocks is not None and not game_over and promoting_move is None:
            ticking_color = gs.friendly_color
            clocks[ticking_color] = max(0.0, clocks[ticking_color] - dt_ms / 1000)
            if clocks[ticking_color] <= 0.0 and flag_fallen is None:
                flag_fallen = ticking_color
                game_over = True

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
                        commit_move(promoting_move)

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
                                    commit_move(move)
                                    sq_selected = None
                                    player_clicks = []
                                break

                        if not move_made and promoting_move is None:
                            player_clicks = player_clicks[1:]

                # Logic when a player clicks inside the move log panel area
                else:
                    menu_btn, review_btn, prev_btn, next_btn, restart_btn, flip_btn = \
                        ui.get_control_button_rects(show_review=game_over)

                    if menu_btn.collidepoint(location):
                        return_to_menu = True
                        running = False

                    elif game_over and review_btn.collidepoint(location):
                        # Post-game review of the finished game, on an
                        # isolated copy; the final position (and its history
                        # browsing) is restored untouched when it closes
                        promoting_move = None
                        outcome = run_review(
                            screen, clock, move_log_font, coord_font, bar_font,
                            _clone_game_for_review(gs)
                        )
                        screen = pg.display.get_surface() or screen
                        if outcome is None:
                            running = False

                    elif restart_btn.collidepoint(location):
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
                        target_index = ui.get_move_log_click_index(
                            location, len(gs.move_log), move_log_font, show_review=game_over
                        )

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
                    commit_move(matched)

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
        forced_result = ('0-1' if flag_fallen == 'w' else '1-0') if flag_fallen is not None else None

        graphics.draw_game_state(screen, gs, valid_moves, sq_selected, board_flipped, coord_font)
        ui.draw_player_bars(screen, gs, bar_font, board_flipped, vs_ai, ai_thinking, clocks)
        ui.draw_move_log(screen, gs, move_log_font, forced_result, show_review=game_over)

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
        elif flag_fallen is not None:
            ui.time_forfeit_banner(screen, flag_fallen)

        pg.display.flip()

    return return_to_menu


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
    graphics.load_eval_icons()
    graphics.cache_coordinate_fonts(coord_font)

    # Loop between the opponent menu, the time-control menu, and the game
    # itself: "Back" on the time-control screen and "Main Menu" mid-game
    # both return to the opponent menu instead of only quitting
    while True:
        choice = run_main_menu(screen, clock, title_font, move_log_font)
        if choice is None:
            break

        if choice == 'analysis':
            # Import screen -> review, looping back to the import screen so
            # several games can be analysed in a row; 'back' returns to the
            # opponent menu and a closed window quits entirely
            quit_requested = False
            while True:
                imported = run_import_menu(screen, clock, title_font, move_log_font)
                if imported == 'quit':
                    quit_requested = True
                    break
                if imported == 'back':
                    break
                assert isinstance(imported, chess_engine.GameState)
                outcome = run_review(screen, clock, move_log_font, coord_font, bar_font, imported)
                screen = pg.display.get_surface() or screen
                if outcome is None:
                    quit_requested = True
                    break
            if quit_requested:
                break
            continue

        mode_key = run_time_control_menu(screen, clock, title_font, move_log_font)
        if mode_key is None:
            break
        if mode_key == ui.BACK_SENTINEL:
            continue

        return_to_menu = run_game(
            screen, clock, move_log_font, coord_font, bar_font, choice == 'ai', mode_key
        )
        screen = pg.display.get_surface() or screen
        if not return_to_menu:
            break

    pg.quit()


if __name__ == '__main__':
    main()
