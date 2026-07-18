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

# Clock-aware time management (step 5 of LICHESS_BOT_PLAN.md). When the host
# sends clock fields instead of explicit limits, we budget a slice of the
# remaining time for this one move and let iterative deepening stop on the
# timer (`SearchTimeout`) rather than on depth.
CLOCK_FRACTION = 30      # spend ~1/30th of the remaining time per move
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

# Persistent transposition table (step 6 of LICHESS_BOT_PLAN.md): shared by
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


def clock_move_budget(remaining_ms: int, increment_ms: int) -> float:
    """
    Compute the thinking-time budget for one move from the game clock.

    The classic heuristic: assume roughly `CLOCK_FRACTION` moves remain in
    the game, so spend that fraction of the remaining time — plus most of
    the per-move increment, which is "free" time that comes back after every
    move. The result is clamped so the engine neither moves instantly with a
    full clock nor flags with a nearly empty one.

    Parameters
    ----------
    remaining_ms : int
        Milliseconds left on the side-to-move's clock.
    increment_ms : int
        Milliseconds added to the clock after each move.

    Returns
    -------
    float
        Time budget in seconds, clamped to [MIN_MOVE_TIME, MAX_MOVE_TIME].
    """
    budget = (remaining_ms / 1000.0 / CLOCK_FRACTION
              + increment_ms / 1000.0 * INCREMENT_WEIGHT)
    return max(MIN_MOVE_TIME, min(MAX_MOVE_TIME, budget))


def parse_go_limits(tokens: list[str], white_to_move: bool) -> tuple[int, float, float]:
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
        movetime = clock_move_budget(remaining, increment)
        # Fund the panic extension from the clock, never endangering it:
        # the ceiling can't exceed a fixed fraction of what's actually left.
        hard = max(movetime, min(PANIC_HARD_FACTOR * movetime,
                                 remaining / 1000.0 / PANIC_CLOCK_DIVISOR))
        if depth is None:
            depth = CLOCK_MAX_DEPTH  # the clock, not the depth, stops the search

    movetime = movetime if movetime is not None else DEFAULT_MOVETIME
    return (depth if depth is not None else DEFAULT_DEPTH,
            movetime,
            hard if hard is not None else movetime)


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

    depth, movetime, hard = parse_go_limits(tokens, gs.white_to_move)
    best = move_finder.find_best_move(gs, max_depth=depth, time_limit=movetime,
                                      tt=transposition_table, hard_limit=hard)
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
