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

    uv run --no-project python -m engine.tools.selfplay        # 20 games (default)
    uv run --no-project python -m engine.tools.selfplay 5      # 5 games

Exits non-zero if any game ends in an illegal move or a crash, so it doubles as
a pre-deploy assertion.
"""
import random
import sys
import time

from engine.board import GameState, Move
from engine.uci import clock_move_budget
from engine.uci_client import (
    EngineClientError,
    UciEngineClient,
    resolve_engine_command,
)
from engine.movegen import generate_legal


# How many games a full run plays, and the per-move search budget. MAX_PLIES
# bounds a single game: draw detection already ends games (50-move / threefold
# fold into an empty move list), but the cap guarantees termination even if
# both engines shuffle forever.
#
# DEPTH used to be 6, described as a "safety cap" on the assumption that the
# clock always ends the search first. Measurement said otherwise: at a 0.2s
# budget, depth 6 completed naturally in 4 of 7 realistic positions — every
# endgame and most late middlegames — leaving the engine idle on the rest of
# its time. That quietly capped what the A/B harness could measure, because a
# faster engine in a cap-bound position has nowhere to spend the speed, so
# speed-oriented changes read as 0 Elo however much they really help. It now
# sits above what the budget can reach, leaving the clock as the only binding
# constraint; it still guarantees termination on a forced line.
DEFAULT_GAMES = 20
MOVETIME = 0.1   # seconds per move
DEPTH = 12       # ply cap set above the budget's reach; the clock binds
MAX_PLIES = 300
BOOK_ATTEMPTS = 100  # redraws allowed before a random opening line is given up on


def random_opening(plies: int, rng: random.Random) -> list[str]:
    """
    Build a random legal opening line, as UCI move strings.

    Used to give a *pair* of games a shared starting position (see
    `engine.tools.sprt`). The line does not need to be balanced or sensible —
    because the pair plays it from both sides, any advantage baked into it
    is handed to each engine exactly once and cancels out. What it must be
    is *legal* and *unfinished*, so both games start from a real position
    with moves still available.

    Parameters
    ----------
    plies : int
        How many half-moves to play out.
    rng : random.Random
        Seeded generator, so a match's openings are reproducible.

    Returns
    -------
    list of str
        `plies` UCI move strings, ending in a position that still has legal
        moves.

    Notes
    -----
    Random play really does stumble into finished games — 8 random plies can
    produce Fool's Mate (``1.b4 e6 2.f3 h5 3.g4 Qh4#``). A book line that ends
    the game hands both halves of the pair a result neither engine played, so
    such lines are discarded and redrawn rather than truncated. Truncating
    would be worse than it looks: it leaves the position one move short of a
    mate that both engines would simply play.
    """
    for _ in range(BOOK_ATTEMPTS):
        gs = GameState()
        line: list[str] = []
        for _ in range(plies):
            moves = generate_legal(gs)
            if not moves:
                break            # dead line; fall through to redraw
            move = rng.choice(moves)
            line.append(move.get_uci_notation())
            gs.make_move(move, annotate=False)
        if len(line) == plies and generate_legal(gs):
            return line
    raise RuntimeError(
        f'could not draw a live {plies}-ply opening in {BOOK_ATTEMPTS} attempts')


def play_game(white: UciEngineClient, black: UciEngineClient,
              depth: int = DEPTH, movetime: float = MOVETIME,
              clock: tuple[float, float] | None = None,
              opening: list[str] | None = None) -> tuple[str, int, str | None]:
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
    opening : list of str, optional
        UCI moves to replay before either engine searches, so that two games
        can share a starting position with the colours swapped. Book moves
        cost no clock time, as on Lichess. They *do* count toward the played
        move total, which is what the time budget uses to age its estimate of
        the moves remaining.

    Returns
    -------
    tuple of (str, int, str or None)
        `(result, plies, failure)`. `result` is one of ``'checkmate'``,
        ``'draw/stalemate'``, ``'move-cap'``, ``'bad-opening'``, or (clock
        mode) ``'flagged'``.
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

    # Replay the shared opening, if this game is half of a colour-reversed
    # pair. These plies are pushed through the same referee path as searched
    # moves, so an illegal book line fails here rather than silently
    # desynchronising the engines from the driver's board.
    for uci_move in opening or ():
        book_legal = {m.get_uci_notation(): m for m in generate_legal(gs)}
        if uci_move not in book_legal:
            return ('bad-opening', len(played),
                    f'opening move {uci_move!r} is not legal after {played}')
        gs.make_move(book_legal[uci_move], annotate=False)
        played.append(uci_move)

    while True:
        moves = generate_legal(gs)  # UI path: sets gs.in_check and folds
        if not moves:                 #   50-move / threefold into an empty list
            return ('checkmate' if gs.in_check else 'draw/stalemate', len(played), None)
        if len(played) >= MAX_PLIES:
            return ('move-cap', len(played), None)

        engine = white if gs.white_to_move else black
        side = 'White' if gs.white_to_move else 'Black'
        mover = 0 if gs.white_to_move else 1

        if clock:
            # Half the plies played are this side's, which is what the budget
            # needs to age its estimate of the moves still to come.
            budget = clock_move_budget(int(remaining[mover] * 1000),
                                       int(clock[1] * 1000),
                                       len(played) // 2)
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
