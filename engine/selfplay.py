"""
Self-play smoke test: two engine processes play full games against each other.

This is the integration counterpart to the perft node-count tests. Perft proves
move *generation* is correct in isolation; this proves whole *games* hold
together — the UCI protocol round-trip, iterative-deepening search, make/unmake,
promotion/castle/en-passant handling, and game-end + draw detection all working
in concert, over hundreds of moves, exactly the way lichess-bot drives the
engine online.

It reuses `engine.uci_client` to spawn real `engine.uci` subprocesses (the same
code path the Lichess bridge uses), so anything that breaks here breaks online
too. The driver itself is the referee: it holds the authoritative `GameState`,
asks the side-to-move engine for a move, and **fails loudly on any illegal move
or engine crash**. Games vary between runs because the search shuffles
equal-scoring root moves (`_root_rng` in `move_finder.py`), so no opening book is
needed to avoid replaying one game 20 times.

Run it (CPython host spawning PyPy engines, when PyPy is available):

    uv run --no-project python -m engine.selfplay        # 20 games (default)
    uv run --no-project python -m engine.selfplay 5      # 5 games

Exits non-zero if any game ends in an illegal move or a crash, so it doubles as
a pre-deploy assertion.
"""
import sys
import time

from engine.chess_engine import GameState, Move
from engine.uci import clock_move_budget
from engine.uci_client import (
    EngineClientError,
    UciEngineClient,
    resolve_engine_command,
)

# How many games a full run plays, and the per-move search budget. The time
# limit (not the depth) is what actually ends each search — DEPTH is only a
# safety cap so a warm PyPy engine can't run away to an absurd depth on a
# forced line. MAX_PLIES bounds a single game: draw detection already ends
# games (50-move / threefold fold into an empty move list), but the cap
# guarantees termination even if both engines shuffle forever.
DEFAULT_GAMES = 20
MOVETIME = 0.1   # seconds per move
DEPTH = 6        # ply cap; the clock stops the search first
MAX_PLIES = 300


def play_game(white: UciEngineClient, black: UciEngineClient,
              depth: int = DEPTH, movetime: float = MOVETIME,
              clock: tuple[float, float] | None = None) -> tuple[str, int, str | None]:
    """
    Play one full game between two engine clients and referee every move.

    The driver keeps the only authoritative `GameState`. Each ply it asks the
    side-to-move engine for its move (sending the full move list so the engine
    replays the game and keeps its own repetition history), then checks that
    move against the legal move list before applying it.

    Parameters
    ----------
    white, black : UciEngineClient
        The two engine subprocesses. `new_game()` is called on both here so
        each starts the game with a cleared transposition table.
    depth : int
        Ply cap passed to each search; the clock normally stops it first.
    movetime : float
        Per-move search budget in seconds. Ignored when `clock` is given.
    clock : tuple of (float, float), optional
        `(base_seconds, increment_seconds)` for a *simulated game clock*.
        The referee then budgets each move with `clock_move_budget` (the
        same formula the UCI adapter uses online), charges the engine the
        wall time it actually spent, credits the increment after the move,
        and rules the game `'flagged'` the moment a side's clock runs out.
        This is what makes time management measurable: an engine that banks
        unused budget gets deeper searches later in the same game, and an
        engine that overspends loses on time — exactly like on Lichess.

    Returns
    -------
    tuple of (str, int, str or None)
        `(result, plies, failure)`. `result` is one of ``'checkmate'``,
        ``'draw/stalemate'``, ``'move-cap'``, or (clock mode) ``'flagged'``.
        For both ``'checkmate'`` and ``'flagged'`` the losing side is the
        side to move after `plies` half-moves — even plies means White.
        `failure` is None on a clean game, or a human-readable string
        describing the illegal move or crash that aborted it (in which case
        `result` is that failure's category).
    """
    white.new_game()
    black.new_game()

    gs = GameState()
    played: list[str] = []
    # Simulated clocks, only meaningful in clock mode
    remaining = [clock[0], clock[0]] if clock else [0.0, 0.0]

    while True:
        moves = gs.get_valid_moves()  # UI path: sets gs.in_check and folds
        if not moves:                 #   50-move / threefold into an empty list
            return ('checkmate' if gs.in_check else 'draw/stalemate', len(played), None)
        if len(played) >= MAX_PLIES:
            return ('move-cap', len(played), None)

        engine = white if gs.white_to_move else black
        side = 'White' if gs.white_to_move else 'Black'
        mover = 0 if gs.white_to_move else 1

        if clock:
            budget = clock_move_budget(int(remaining[mover] * 1000),
                                       int(clock[1] * 1000))
            started = time.monotonic()
        else:
            budget = movetime

        try:
            uci = engine.search_from_moves(played, depth, budget)
        except EngineClientError as exc:
            return ('crash', len(played), f'{side} engine crashed after {played}: {exc}')

        if clock:
            # Charge real wall time, credit the increment — and flag before
            # the move is recorded, so the loser is the side to move at
            # `plies`, same parity rule as checkmate.
            remaining[mover] -= time.monotonic() - started
            if remaining[mover] <= 0:
                return ('flagged', len(played), None)
            remaining[mover] += clock[1]

        legal: dict[str, Move] = {m.get_uci_notation(): m for m in moves}
        if uci not in legal:
            return ('illegal', len(played),
                    f'{side} played illegal {uci!r}; legal: {sorted(legal)} after {played}')

        gs.make_move(legal[uci], annotate=False)
        played.append(uci)


def main() -> None:
    """
    Run the self-play smoke test and exit non-zero on any illegal move or crash.

    Returns
    -------
    None
    """
    games = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GAMES

    # Spawn two real UCI subprocesses (PyPy when available, else this Python),
    # reused across all games so PyPy's JIT stays warm.
    command = resolve_engine_command() or [sys.executable, '-m', 'engine.uci']
    print(f'Engine command: {" ".join(command)}')
    print(f'Playing {games} games at {MOVETIME}s/move (depth cap {DEPTH})\n')

    white = UciEngineClient(command)
    black = UciEngineClient(command)

    failures: list[str] = []
    try:
        for i in range(1, games + 1):
            result, plies, failure = play_game(white, black)
            status = 'ok' if failure is None else 'FAIL'
            print(f'game {i:>3}: {result:<14} {plies:>3} plies  [{status}]')
            if failure is not None:
                failures.append(f'game {i}: {failure}')
                # A crash leaves the pipes in an unknown state; stop rather than
                # cascade misleading failures through the remaining games.
                if result == 'crash':
                    break
    finally:
        white.close()
        black.close()

    print()
    if failures:
        print(f'FAIL: {len(failures)} game(s) with illegal moves or crashes:')
        for line in failures:
            print(f'  - {line}')
        sys.exit(1)
    print(f'PASS: {games} games, 0 illegal moves, 0 crashes')


if __name__ == '__main__':
    main()
