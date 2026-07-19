"""
UCI (Universal Chess Interface) adapter for the PyCheckmate engine.

This module lets the engine talk to any UCI-compatible host: chess GUIs
(Cute Chess, Arena, BanksiaGUI) and, most importantly, the `lichess-bot`
bridge that connects UCI engines to the Lichess Bot API.

Run it from the project root and type commands, or point a GUI/bridge at it:

    python -m engine.uci

Supported commands: `uci`, `isready`, `ucinewgame`,
`position startpos|fen <fen> [moves <uci>...]`,
`go [depth N] [movetime MS] [wtime MS btime MS winc MS binc MS]`, `quit`.
"""
import sys

from engine import chess_engine, move_finder

ENGINE_NAME = 'PyCheckmate'
ENGINE_AUTHOR = 'Lucas Pham'

# Search defaults when the host gives no explicit limits
DEFAULT_DEPTH = 5
DEFAULT_MOVETIME = 5.0  # seconds

# Clock-aware time management. When the host sends clock fields instead of
# explicit limits, we budget a slice of the remaining time for this one move
# and let iterative deepening stop on the timer (`SearchTimeout`) rather than
# on depth.
#
# The budget divides the remaining clock by an *estimate of the moves still to
# play*, rather than by a constant. A constant divisor is what this used to do
# (`remaining / 30`), and it has a subtle failure: dividing by a fixed number
# decays geometrically, so the clock is never actually spent. Replaying the
# bot's 16 online games (`engine.tm_replay`) showed it finishing *sixty-move*
# games with 28-48% of its clock unused — it was effectively playing a faster
# time control than the one it had been given, while blundering in positions
# where more thought was available and simply not taken.
#
# The same replay says where that reclaimed time belongs. Grading every move
# the bot has played puts 73% of its blunders, mistakes and missed wins in
# moves 21-40 (8% and 7% error rates, against 1% in moves 1-20 where the
# opening book answers for us). So the curve is aimed to peak across that
# band: EXPECTED_GAME_MOVES is set past a typical game so the estimate stays
# generous through it, and MIN_MOVES_TO_GO keeps the divisor sane afterwards.
EXPECTED_GAME_MOVES = 52  # our own moves; real games ran 26-67, averaging 45
MIN_MOVES_TO_GO = 18      # floor on the estimate once the game runs long

# Two emergency tiers. Spending harder mid-game necessarily drains the clock
# faster, which without a brake would flag a marathon game; these restore
# exactly the survival the old constant divisor had (both rules reach move
# ~124 of a 5+0 game). They deliberately trigger only on a genuinely low
# clock: an earlier draft engaged below 60s, which in a 5+0 game meant it
# started hoarding around move 35 — safe, and precisely inside the band where
# the errors actually happen.
LOW_CLOCK_SECONDS = 25.0
LOW_CLOCK_MOVES_TO_GO = 30
PANIC_CLOCK_SECONDS = 10.0
PANIC_CLOCK_MOVES_TO_GO = 60

# Time the engine never gets to use: Lichess round-trip, the bridge, and
# process scheduling. Budgeting the full clock ignores it and loses games on
# time that were never lost on the board, so it comes off the top.
MOVE_OVERHEAD = 0.15     # seconds per move lost outside the search
CLOCK_RESERVE = 1.0      # never plan to spend the last second

INCREMENT_WEIGHT = 0.8   # plus most of the increment (keep a safety margin)
MIN_MOVE_TIME = 0.05     # seconds — always think at least a tick
MAX_MOVE_TIME = 20.0     # seconds — cap so long clocks aren't drained early

# Panic-extension ceiling on the clock path: when the search sees its score
# collapse it may think up to PANIC_HARD_FACTOR times the normal budget —
# but never more than 1/PANIC_CLOCK_DIVISOR of the remaining clock, so a
# string of panics can't flag the game. Explicit `movetime`/`depth` limits
# are promises to the host and get no extension.
PANIC_HARD_FACTOR = 2.5
PANIC_CLOCK_DIVISOR = 8
CLOCK_MAX_DEPTH = 64     # effectively unlimited: the clock is the real cap

# Persistent transposition table: shared by
# every `go` of the same game so each search starts warm from the previous
# moves' results, and cleared on `ucinewgame`. Zobrist keys identify
# positions absolutely, so entries stay valid as the game advances.
transposition_table: move_finder.TTable = {}
# Safety valve for very long games: one entry is roughly a hundred bytes, so
# this bounds the table at a few hundred MB before it is simply rebuilt.
TT_MAX_ENTRIES = 2_000_000


def apply_uci_move(gs: chess_engine.GameState, uci_str: str) -> bool:
    """
    Apply a move given in UCI coordinate notation to the game state.

    Parameters
    ----------
    gs : chess_engine.GameState
        The game state to mutate.
    uci_str : str
        The move in UCI notation (e.g., 'e2e4', 'e7e8q').

    Returns
    -------
    bool
        True if the move matched a legal move and was applied, else False.
    """
    for move in gs.get_valid_moves():
        if move.get_uci_notation() == uci_str:
            gs.make_move(move, annotate=False)
            return True
    return False


def build_position(tokens: list[str]) -> chess_engine.GameState:
    """
    Build a GameState from the arguments of a UCI `position` command.

    Parameters
    ----------
    tokens : list of str
        The command arguments after 'position', e.g.
        ['startpos', 'moves', 'e2e4', 'e7e5'] or
        ['fen', <6 FEN fields...>, 'moves', ...].

    Returns
    -------
    chess_engine.GameState
        The reconstructed game state after all listed moves.

    Raises
    ------
    ValueError
        If the position description or one of the moves is invalid.
    """
    if not tokens:
        raise ValueError('position: missing arguments')

    if tokens[0] == 'startpos':
        gs = chess_engine.GameState()
        move_tokens = tokens[2:] if len(tokens) > 1 and tokens[1] == 'moves' else []
    elif tokens[0] == 'fen':
        # FEN is 6 space-separated fields; 'moves' may follow
        if 'moves' in tokens:
            moves_at = tokens.index('moves')
            fen = ' '.join(tokens[1:moves_at])
            move_tokens = tokens[moves_at + 1:]
        else:
            fen = ' '.join(tokens[1:])
            move_tokens = []
        gs = chess_engine.GameState.from_fen(fen)
    else:
        raise ValueError(f'position: unknown mode {tokens[0]!r}')

    for uci_move in move_tokens:
        if not apply_uci_move(gs, uci_move):
            raise ValueError(f'position: illegal move {uci_move!r}')
    return gs


def moves_to_go(remaining_s: float, moves_played: int) -> int:
    """
    Estimate how many more moves this side still has to play.

    Starts from `EXPECTED_GAME_MOVES` and counts down with the moves already
    made, so the budget stays generous through the middlegame instead of
    shrinking from move one. Once the game outlasts that estimate the count
    stops at `MIN_MOVES_TO_GO`, and a genuinely low clock overrides both — at
    that point surviving matters more than thinking.

    Parameters
    ----------
    remaining_s : float
        Seconds left on this side's clock.
    moves_played : int
        How many moves this side has already played.

    Returns
    -------
    int
        Divisor for the remaining clock: bigger means spend less now.
    """
    estimate = max(MIN_MOVES_TO_GO, EXPECTED_GAME_MOVES - moves_played)
    if remaining_s < PANIC_CLOCK_SECONDS:
        return max(estimate, PANIC_CLOCK_MOVES_TO_GO)
    if remaining_s < LOW_CLOCK_SECONDS:
        return max(estimate, LOW_CLOCK_MOVES_TO_GO)
    return estimate


def clock_move_budget(remaining_ms: int, increment_ms: int,
                      moves_played: int = 0) -> float:
    """
    Compute the thinking-time budget for one move from the game clock.

    Spends the remaining clock divided by an estimate of the moves left to
    play (see `moves_to_go`), plus most of the increment — which is "free"
    time that comes back after every move. `MOVE_OVERHEAD` and
    `CLOCK_RESERVE` come off the top first: neither is time the search can
    actually use, and budgeting them away is what stops the engine losing on
    time in a position it was winning on the board.

    Parameters
    ----------
    remaining_ms : int
        Milliseconds left on the side-to-move's clock.
    increment_ms : int
        Milliseconds added to the clock after each move.
    moves_played : int, optional
        Moves this side has already played, used to age the estimate of how
        many remain. Default 0 (treat the position as a game start).

    Returns
    -------
    float
        Time budget in seconds, clamped to [MIN_MOVE_TIME, MAX_MOVE_TIME].
    """
    remaining_s = remaining_ms / 1000.0
    usable = max(0.0, remaining_s - MOVE_OVERHEAD - CLOCK_RESERVE)
    budget = (usable / moves_to_go(remaining_s, moves_played)
              + increment_ms / 1000.0 * INCREMENT_WEIGHT)
    return max(MIN_MOVE_TIME, min(MAX_MOVE_TIME, budget))


def parse_go_limits(tokens: list[str], white_to_move: bool,
                    moves_played: int = 0) -> tuple[int, float, float]:
    """
    Derive search limits (max depth, time budget) from `go` arguments.

    Explicit limits win: `depth N` fixes the depth and `movetime MS` fixes
    the time. Otherwise, if the host sent clock fields (`wtime`/`btime`/
    `winc`/`binc` — Lichess sends these on every `go`), the side to move's
    clock becomes a per-move budget via `clock_move_budget`, and the depth
    is left effectively unlimited so the timer — not the depth — ends the
    iterative-deepening search. With no limits at all, defaults apply.

    Parameters
    ----------
    tokens : list of str
        The command arguments after 'go' (e.g., ['depth', '4'] or
        ['wtime', '600000', 'btime', '600000', 'winc', '5000', 'binc', '5000']).
    white_to_move : bool
        Whose clock applies: True reads wtime/winc, False reads btime/binc.

    Returns
    -------
    tuple of (int, float, float)
        `(max_depth, time_limit, hard_limit)` in plies and seconds, ready to
        pass to `find_best_move`. `hard_limit` exceeds `time_limit` only on
        the clock path, where it funds the search's panic extension; with
        explicit limits (or no limits) the two are equal and no extension
        can happen.
    """
    values: dict[str, int] = {}
    for i, token in enumerate(tokens):
        if token in ('depth', 'movetime', 'wtime', 'btime', 'winc', 'binc') \
                and i + 1 < len(tokens):
            values[token] = int(tokens[i + 1])

    depth = values.get('depth')
    movetime = values['movetime'] / 1000.0 if 'movetime' in values else None
    hard = None

    remaining = values.get('wtime' if white_to_move else 'btime')
    if movetime is None and remaining is not None:
        increment = values.get('winc' if white_to_move else 'binc', 0)
        movetime = clock_move_budget(remaining, increment, moves_played)
        # Fund the panic extension from the clock, never endangering it:
        # the ceiling can't exceed a fixed fraction of what's actually left.
        hard = max(movetime, min(PANIC_HARD_FACTOR * movetime,
                                 remaining / 1000.0 / PANIC_CLOCK_DIVISOR))

    # *Any* explicit time budget — `movetime` as well as a clock — means the
    # timer is the intended stopping rule, so the depth must not silently cap
    # it first. This used to apply only on the clock path, which left
    # `go movetime 4000` searching to DEFAULT_DEPTH (5) and returning after
    # ~450ms of its 4s. Lichess was unaffected (the bridge sends wtime/btime),
    # but every movetime-driven test was measuring a depth-capped engine.
    # DEFAULT_DEPTH now applies only when there is no time information at all.
    if depth is None and movetime is not None:
        depth = CLOCK_MAX_DEPTH

    movetime = movetime if movetime is not None else DEFAULT_MOVETIME
    return (depth if depth is not None else DEFAULT_DEPTH,
            movetime,
            hard if hard is not None else movetime)


def report_iteration(depth: int, score: int, nodes: int, elapsed: float,
                     move: move_finder.MoveTuple,
                     board: list[list[int]]) -> None:
    """
    Print one UCI `info` line for a completed deepening iteration.

    Without these lines the engine is a black box: Lichess shows no evaluation,
    and there is no way to tell from a real game what depth was reached or
    whether the clock was being spent. Every offline diagnostic this project
    has needed so far had to reconstruct that information by re-analysing the
    finished PGN, which is both slow and unable to see what the engine
    *actually* thought at the time.

    Parameters
    ----------
    depth : int
        Iteration depth just completed.
    score : int
        Score in centipawns from the side to move's perspective — which is
        already the perspective UCI wants, so no conversion is needed.
    nodes : int
        Nodes searched so far this move.
    elapsed : float
        Seconds spent so far this move.
    move : move_finder.MoveTuple
        Best move of this iteration.
    board : list of list of int
        Root board, needed to render the move in UCI notation.

    Returns
    -------
    None
    """
    if abs(score) >= move_finder.MATE_THRESHOLD:
        # UCI reports mate in *moves*, signed for the side to move, while the
        # search stores plies-from-root inside the mate score.
        plies = move_finder.CHECKMATE_SCORE - abs(score)
        moves_to_mate = (plies + 1) // 2
        report = f'mate {moves_to_mate if score > 0 else -moves_to_mate}'
    else:
        report = f'cp {score}'

    best_uci = chess_engine.Move.from_ai_tuple(move, board).get_uci_notation()
    nps = int(nodes / elapsed) if elapsed > 0 else 0
    print(f'info depth {depth} score {report} nodes {nodes} nps {nps} '
          f'time {int(elapsed * 1000)} pv {best_uci}')
    sys.stdout.flush()


def handle_go(gs: chess_engine.GameState, tokens: list[str]) -> str:
    """
    Execute a UCI `go` command and return the chosen move in UCI notation.

    Parameters
    ----------
    gs : chess_engine.GameState
        The game state to search from.
    tokens : list of str
        The command arguments after 'go' (e.g., ['depth', '4'],
        ['movetime', '3000'], or Lichess-style clock fields — see
        `parse_go_limits`).

    Returns
    -------
    str
        The best move in UCI notation, or '0000' when no legal move exists.
    """
    if len(transposition_table) > TT_MAX_ENTRIES:
        transposition_table.clear()

    # Each side has played half the plies in the log; the budget uses that to
    # age its estimate of how many moves are still to come.
    depth, movetime, hard = parse_go_limits(tokens, gs.white_to_move,
                                            len(gs.move_log) // 2)
    board = gs.board
    best = move_finder.find_best_move(
        gs, max_depth=depth, time_limit=movetime, tt=transposition_table,
        hard_limit=hard,
        on_iteration=lambda d, s, n, t, m: report_iteration(d, s, n, t, m, board))
    if best is None:
        return '0000'  # UCI null move: no legal moves available

    return chess_engine.Move.from_ai_tuple(best, gs.board).get_uci_notation()


def main() -> None:
    """
    Run the UCI read-eval-print loop over stdin/stdout until `quit`.

    Returns
    -------
    None
    """
    gs = chess_engine.GameState()

    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        command, args = parts[0], parts[1:]

        if command == 'uci':
            print(f'id name {ENGINE_NAME}')
            print(f'id author {ENGINE_AUTHOR}')
            print('uciok')

        elif command == 'isready':
            print('readyok')

        elif command == 'ucinewgame':
            gs = chess_engine.GameState()
            # A new game means new positions: drop the accumulated table
            transposition_table.clear()

        elif command == 'position':
            try:
                gs = build_position(args)
            except ValueError as exc:
                print(f'info string {exc}')

        elif command == 'go':
            best_uci = handle_go(gs, args)
            print(f'bestmove {best_uci}')

        elif command == 'quit':
            break

        sys.stdout.flush()


if __name__ == '__main__':
    main()
