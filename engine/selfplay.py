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

from engine.chess_engine import GameState, Move
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


def play_game(white: UciEngineClient, black: UciEngineClient) -> tuple[str, int, str | None]:
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

    Returns
    -------
    tuple of (str, int, str or None)
        `(result, plies, failure)`. `result` is one of ``'checkmate'``,
        ``'draw/stalemate'``, or ``'move-cap'``. `failure` is None on a clean
        game, or a human-readable string describing the illegal move or crash
        that aborted it (in which case `result` is that failure's category).
    """
    white.new_game()
    black.new_game()

    gs = GameState()
    played: list[str] = []

    while True:
        moves = gs.get_valid_moves()  # UI path: sets gs.in_check and folds
        if not moves:                 #   50-move / threefold into an empty list
            return ('checkmate' if gs.in_check else 'draw/stalemate', len(played), None)
        if len(played) >= MAX_PLIES:
            return ('move-cap', len(played), None)

        engine = white if gs.white_to_move else black
        side = 'White' if gs.white_to_move else 'Black'

        try:
            uci = engine.search_from_moves(played, DEPTH, MOVETIME)
        except EngineClientError as exc:
            return ('crash', len(played), f'{side} engine crashed after {played}: {exc}')

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
