"""
Texel tuning: fit the evaluation's constants to real game outcomes.

`docs/ENGINE_V2_PLAN.md` mistake M7 is that the evaluation carries ~34 hand-picked
numbers and not one of them was ever fitted. This is the tool that fits them.

The method (Peter Osterlund's, hence "Texel") is one idea. Take a large set of
positions whose games are known to have been won, drawn or lost. Squash each
position's evaluation through a sigmoid to turn centipawns into a predicted
win probability, then choose the weights that minimize the squared error
between that prediction and what actually happened. No chess knowledge is
required to run it: the games supply the ground truth, and the fit decides
what a bishop pair is worth.

    E = mean over positions of (result - sigmoid(-K * score / 400)) ** 2

`result` is 1.0, 0.5 or 0.0 from White's perspective, and `score` is
`evaluate()` from White's perspective, so the two agree on sign by
construction. K sets how sharply centipawns convert to probability and is
fitted once, before any weight is touched, because every later comparison
depends on it.

## Where the positions come from, and a mistake worth remembering

Texel tuning wants hundreds of thousands of positions. Fitting ~30 parameters
on a small set will happily learn the noise in it, and the resulting numbers
look like an improvement while being worth nothing — exactly the failure mode
`CLAUDE.md` was written about.

Two things are therefore built in rather than optional:

- **The split is by group, not by position.** Positions from one game are
  strongly correlated (same pawn structure, same material, same result), so a
  position-level split leaks the answer across it and makes validation error
  meaninglessly optimistic. A group is one game for PGN input, and one
  contiguous block of the file for EPD input, where neighbouring lines come
  from the same game.
- **A parameter change is only kept if it improves error on groups the fit has
  never seen.** Training error always falls; that is what fitting does. Only
  the held-out number is evidence.

This tool was first written to read only the bot's own PGNs, and on 127 games
-> **3,231 quiet positions** (about 111 per parameter) it produced pure noise:
run to convergence, held-out error *rose* to 0.1401, worse than not tuning at
all, and the values were chess nonsense — bishop below knight, outposts
penalized, every passed-pawn bonus negative. Repeating across four splits gave
-3.1%, -6.2%, +0.0% and -17.8%.

The conclusion recorded here at the time was "re-run once the bot has played
several hundred more games". **That was wrong**, and it is the more useful
lesson of the two. Because the loader read PGNs, "we need more data" silently
became "we need to play more games" — so the fix looked like eight days of
waiting. Public labeled sets have existed for years, and a full 600-game run
(~15k positions) would still have been ~50x short of what the method wants.
Texel's own paper used 8.8M positions from 64,000 games.

**When a tool seems to need data you do not have, check what its loader can
read before you go and collect any.**

The default input is now `quiet-labeled.epd` — 725,000 already-quiet positions
labeled with game results, ~39 MB, no relation to our own games. It is not
committed; fetch it once with:

    curl -Lo ~/.local/share/pycheckmate/quiet-labeled.epd \\
      https://raw.githubusercontent.com/KierenP/ChessTrainingSets/master/quiet-labeled.epd

Even so, treat the output as a hypothesis to be measured by real games, not as
a result: applying these values is a behavior change, and `PIECE_VALUES` is
shared with the search's static exchange evaluation, so it moves move ordering
as well as scoring. That needs an SPRT, not a held-out error number.

Usage
-----
    PYTHONPATH=. uv run --no-project -p pypy3.11 python -m engine.tools.tune
    PYTHONPATH=. uv run --no-project python -m engine.tools.tune --games-dir DIR

PyPy is worth the flag here: `evaluate()` is 2.25 us there against 20.1 us on
CPython, and this loop does nothing else.

The real ceiling is memory, not time. A `GameState` costs 5.6 KB under PyPy
(6.6 KB on CPython), so the full 725k set would want ~3.9 GB while the default
sample of 200,000 holds ~1.1 GB. This machine has 8 GB, which is why running
two seeds side by side is the cap — see the parallel recipe in `CLAUDE.md`.
Swapping matters more than it looks: if the bot is playing, a machine that
starts paging can lose a rated game on time, which is worse than any amount of
shallow search.
"""
import argparse
import glob
import math
import os
import random
import sys
from typing import Callable

from engine import eval as ev
from engine import pgn
from engine.board import GameState
from engine.movegen import generate_legal

# Where lichess-bot writes its PGNs. Every game the bot has played is training
# data, which is the one advantage of having run it for real.
DEFAULT_GAMES_DIR = os.path.expanduser(
    '~/PycharmProjects/lichess-bot/game_records')

# The public labeled set, kept beside the other generated data rather than in
# the repo: it is 39 MB of someone else's positions.
DEFAULT_EPD = os.path.expanduser(
    '~/.local/share/pycheckmate/quiet-labeled.epd')

# How many EPD lines make one group for the train/validation split. Neighbouring
# lines in these files come from the same game, so splitting whole blocks keeps
# a game's correlated positions on one side of the cut. Only the two lines at a
# block's edges can straddle a game, which at this size is noise.
EPD_BLOCK_SIZE = 512

# Positions sampled by default. The cap is memory, not time: ~6.6 KB per
# GameState puts the full 725k file at ~4.6 GB, while 200,000 costs ~1.3 GB and
# still gives ~6,450 positions per parameter.
DEFAULT_EPD_LIMIT = 200_000

# Positions from the opening book teach nothing about evaluation — the moves
# were not ours and the position is still balanced by construction.
SKIP_OPENING_PLIES = 16

# Fraction of *games* held out. Games, not positions: see the module docstring.
VALIDATION_FRACTION = 0.25

# Seeded so a tuning run is reproducible and two runs can be compared.
SPLIT_SEED = 20260722

RESULT_SCORE = {'1-0': 1.0, '0-1': 0.0, '1/2-1/2': 0.5}

# One unit of the train/validation split: positions that belong together, and
# the result each one is labeled with. A PGN game shares a single result across
# all its positions; an EPD block carries one per line. Keeping the results per
# position is what lets both loaders feed the same splitter.
Group = tuple[list[GameState], list[float]]


def sigmoid(score: float, k: float) -> float:
    """
    Convert a centipawn score into a predicted win probability for White.

    Parameters
    ----------
    score : float
        Evaluation in centipawns, from White's perspective.
    k : float
        Scaling constant; larger means centipawns translate to certainty
        faster.

    Returns
    -------
    float
        Predicted score in [0, 1].
    """
    return 1.0 / (1.0 + math.pow(10.0, -k * score / 400.0))


def load_positions(games_dir: str, limit: int | None = None) -> list[Group]:
    """
    Replay every PGN in a directory into positions labeled by game result.

    Returns positions grouped *per game* so the caller can split by game.
    Positions are filtered to the ones a static evaluation can say anything
    useful about: past the opening book, not in check, and with no capture
    available. That last test is a cheap stand-in for the usual "quiet
    position" filter — a position with a hanging piece is scored by tactics
    the evaluation cannot see, so including it teaches the fit nonsense.

    Parameters
    ----------
    games_dir : str
        Directory of `.pgn` files.
    limit : int or None, optional
        Stop after this many games. Useful for a quick smoke run.

    Returns
    -------
    list of Group
        One entry per game: its quiet positions, and the game's result repeated
        once per position, as a score from White's perspective.
    """
    games: list[Group] = []
    paths = sorted(glob.glob(os.path.join(games_dir, '*.pgn')))
    if limit is not None:
        paths = paths[:limit]

    for path in paths:
        try:
            with open(path, encoding='utf-8') as handle:
                text = handle.read()
        except OSError:
            continue
        result = None
        for line in text.splitlines():
            if line.startswith('[Result '):
                result = line.split('"')[1]
                break
        if result not in RESULT_SCORE:
            continue

        try:
            final = pgn.game_from_pgn(text)
        except Exception:            # noqa: BLE001 - a bad PGN is skipped
            continue

        # Replay from the start, keeping a snapshot of each quiet position.
        moves = list(final.move_log)
        board = GameState()
        positions: list[GameState] = []
        for ply, move in enumerate(moves):
            board.make_move(move, annotate=False)
            if ply < SKIP_OPENING_PLIES:
                continue
            legal = generate_legal(board, for_ai=True)
            if not legal or board.in_check:
                continue
            if any(board.board[m[2]][m[3]] != 0 or m[4] >= 2 for m in legal):
                continue          # a capture is available: not quiet
            positions.append(GameState.from_fen(board.to_fen()))
        if positions:
            games.append((positions, [RESULT_SCORE[result]] * len(positions)))
    return games


def load_epd(path: str, limit: int | None = DEFAULT_EPD_LIMIT,
             block_size: int = EPD_BLOCK_SIZE) -> list[Group]:
    """
    Read a labeled EPD file into position groups.

    The expected line is a FEN followed by the game result in EPD's `c9` field:

        rn2kb1r/ppp1pp1p/... b KQkq - c9 "0-1";

    These files carry only the first four FEN fields, which `GameState.from_fen`
    already accepts — it defaults the half-move clock, and nothing here depends
    on it because `evaluate()` is a pure function of the position.

    Sampling takes every Nth *block* rather than every Nth line, and so keeps
    two properties at once: blocks stay contiguous, so a game's positions are
    not split across the train/validation cut, and the blocks taken are spread
    across the whole file instead of being a prefix of it.

    Parameters
    ----------
    path : str
        Path to the `.epd` file.
    limit : int or None, optional
        Approximate cap on positions returned. None reads the whole file, which
        for a 725k-line set needs roughly 4.6 GB.
    block_size : int, optional
        Lines per group.

    Returns
    -------
    list of Group
        Groups of positions with their per-position results.
    """
    with open(path, encoding='utf-8') as handle:
        lines = [line for line in handle if ' c9 ' in line]

    blocks = [lines[i:i + block_size]
              for i in range(0, len(lines), block_size)]
    if limit is not None and limit < len(lines):
        # Index the blocks we want directly rather than striding and then
        # truncating. `blocks[::step][:wanted]` looks equivalent but is not:
        # integer division always over-strides, so the `[:wanted]` cut throws
        # away the tail the stride was there to reach. At the 200k default that
        # left the last 17.6% of the file unreachable at *every* seed, because
        # --seed only reshuffles blocks that were already selected.
        wanted = max(1, limit // block_size)
        blocks = [blocks[i * len(blocks) // wanted] for i in range(wanted)]

    groups: list[Group] = []
    for block in blocks:
        positions: list[GameState] = []
        results: list[float] = []
        for line in block:
            fen, _, tail = line.partition(' c9 ')
            result = tail.strip().rstrip(';').strip('"')
            if result not in RESULT_SCORE:
                continue
            try:
                position = GameState.from_fen(fen)
            except (ValueError, KeyError, IndexError):
                continue          # a malformed line is skipped, not fatal
            positions.append(position)
            results.append(RESULT_SCORE[result])
        if positions:
            groups.append((positions, results))
    return groups


def mean_squared_error(positions: list[GameState], results: list[float],
                       k: float) -> float:
    """
    Average squared error of the current evaluation over a position set.

    Parameters
    ----------
    positions : list of GameState
        Positions to score.
    results : list of float
        Game results aligned with `positions`, from White's perspective.
    k : float
        Sigmoid scaling constant.

    Returns
    -------
    float
        Mean squared error.
    """
    # The cache is keyed on the Zobrist key and knows nothing about the
    # weights, so it must be dropped whenever they change. Cheaper to clear
    # once per pass than to reason about staleness.
    ev._EVAL_CACHE.clear()
    total = 0.0
    for position, result in zip(positions, results):
        diff = result - sigmoid(ev.evaluate(position), k)
        total += diff * diff
    return total / len(positions)


def fit_k(positions: list[GameState], results: list[float]) -> float:
    """
    Find the sigmoid scale that best fits the *current* weights.

    Done before any weight is tuned, and never again: K decides how a
    centipawn converts to a win probability, so re-fitting it mid-run would
    move the target the weights are being fitted against.

    Parameters
    ----------
    positions : list of GameState
        Training positions.
    results : list of float
        Their game results.

    Returns
    -------
    float
        The best K found by a coarse-then-fine scan.
    """
    best_k, best_error = 1.0, float('inf')
    for k in [x / 100.0 for x in range(20, 300, 10)]:
        error = mean_squared_error(positions, results, k)
        if error < best_error:
            best_k, best_error = k, error
    for k in [best_k - 0.09 + x / 100.0 for x in range(19)]:
        if k <= 0:
            continue
        error = mean_squared_error(positions, results, k)
        if error < best_error:
            best_k, best_error = k, error
    return best_k


class Parameter:
    """
    One tunable number, with the plumbing to read and write it.

    Some of the evaluation's constants are plain module-level ints and some
    live inside tuples (mobility by piece type, the passed-pawn ladder by
    rank). A uniform getter/setter pair keeps the search loop from caring
    which.

    Attributes
    ----------
    name : str
        Human-readable label, used in the report.
    get : callable
        Returns the current value.
    set : callable
        Writes a new value.
    step : int
        Initial step size for the coordinate descent.
    """

    def __init__(self, name: str, get: Callable[[], int],
                 set_: Callable[[int], None], step: int = 8) -> None:
        self.name = name
        self.get = get
        self.set = set_
        self.step = step


def _scalar(name: str, step: int = 8) -> Parameter:
    """
    Build a Parameter for a plain module-level constant in `engine.eval`.

    Parameters
    ----------
    name : str
        Attribute name on the eval module.
    step : int, optional
        Initial step size.

    Returns
    -------
    Parameter
        The wrapped parameter.
    """
    return Parameter(name,
                     lambda: getattr(ev, name),
                     lambda v: setattr(ev, name, v),
                     step)


def _element(name: str, index: int, step: int = 8) -> Parameter:
    """
    Build a Parameter for one slot of a tuple constant in `engine.eval`.

    Parameters
    ----------
    name : str
        Attribute name of the tuple on the eval module.
    index : int
        Which slot to tune.
    step : int, optional
        Initial step size.

    Returns
    -------
    Parameter
        The wrapped parameter.
    """
    def get() -> int:
        return getattr(ev, name)[index]

    def set_(value: int) -> None:
        current = list(getattr(ev, name))
        current[index] = value
        setattr(ev, name, tuple(current))

    return Parameter(f'{name}[{index}]', get, set_, step)


def parameters() -> list[Parameter]:
    """
    The set of constants this tool fits.

    Piece-square tables are deliberately excluded. They are 384 more numbers,
    and 8,700 positions cannot support fitting them — the tables would simply
    memorize our own games. The scalars below are the terms whose values were
    guessed outright.

    `PIECE_VALUES` is included but only for the four pieces, and it is worth
    knowing that it is shared with static exchange evaluation in the search:
    changing it moves move ordering as well as scoring.

    Returns
    -------
    list of Parameter
        Parameters to fit, in a stable order.
    """
    params = [
        _element('PIECE_VALUES', 2, step=10),   # knight
        _element('PIECE_VALUES', 3, step=10),   # bishop
        _element('PIECE_VALUES', 4, step=15),   # rook
        _element('PIECE_VALUES', 5, step=25),   # queen
        _scalar('BISHOP_PAIR_BONUS'),
        _scalar('BAD_BISHOP_PENALTY', step=2),
        _scalar('KNIGHT_OUTPOST_BONUS'),
        _scalar('ROOK_SEMI_OPEN_FILE_BONUS', step=4),
        _scalar('ROOK_OPEN_FILE_BONUS', step=4),
        _scalar('ROOK_ON_SEVENTH_BONUS', step=4),
        _scalar('DOUBLED_PAWN_PENALTY', step=4),
        _scalar('ISOLATED_PAWN_PENALTY', step=4),
        _scalar('KING_SHIELD_BONUS', step=4),
    ]
    params += [_element('MOBILITY_BONUS', i, step=1) for i in (2, 3, 4, 5)]
    params += [_element('PASSED_PAWN_BONUS', r, step=8) for r in range(1, 7)]
    params += [_element('PASSED_PAWN_BONUS_END', r, step=8) for r in range(1, 7)]
    return params


def tune(train: tuple[list[GameState], list[float]],
         valid: tuple[list[GameState], list[float]],
         k: float, max_rounds: int) -> list[tuple[str, int, int]]:
    """
    Coordinate descent over the parameter set.

    Each round tries every parameter up and down by its step size, keeping a
    move only if it lowers *training* error. When a full round changes
    nothing, the step sizes halve; when they reach zero, the fit is done.

    Validation error is measured and reported each round but never steers the
    search — that is what makes it evidence rather than another thing being
    fitted.

    Parameters
    ----------
    train : tuple
        Training positions and their results.
    valid : tuple
        Held-out positions and their results.
    k : float
        Sigmoid scale, already fitted.
    max_rounds : int
        Safety cap on rounds.

    Returns
    -------
    list of (str, int, int)
        Name, original value and fitted value for every parameter that moved.
    """
    params = parameters()
    original = {p.name: p.get() for p in params}
    steps = {p.name: p.step for p in params}

    best = mean_squared_error(*train, k)
    best_valid = mean_squared_error(*valid, k)
    # Early stopping. Training error falls forever — that is what fitting is —
    # so the fit is kept at its best *held-out* point, not its last one. On
    # this dataset that distinction is the whole result: run to convergence
    # and validation error ends up worse than never having tuned.
    best_snapshot = {p.name: p.get() for p in params}
    print(f'  start   train {best:.6f}   valid {best_valid:.6f}')

    for round_index in range(1, max_rounds + 1):
        improved = False
        for param in params:
            step = steps[param.name]
            if step == 0:
                continue
            base = param.get()
            for candidate in (base + step, base - step):
                param.set(candidate)
                error = mean_squared_error(*train, k)
                if error < best:
                    best, improved = error, True
                    break
                param.set(base)
        valid_error = mean_squared_error(*valid, k)
        marker = ''
        if valid_error < best_valid:
            best_valid = valid_error
            best_snapshot = {p.name: p.get() for p in params}
            marker = '  <- best held-out'
        print(f'  round {round_index:2}  train {best:.6f}   '
              f'valid {valid_error:.6f}{marker}')
        if not improved:
            if all(s == 0 for s in steps.values()):
                break
            steps = {name: s // 2 for name, s in steps.items()}

    # Roll back to the best held-out point.
    for param in params:
        param.set(best_snapshot[param.name])
    print(f'  kept the round with valid {best_valid:.6f} '
          f'(start was {mean_squared_error(*valid, k):.6f} after rollback)')

    return [(p.name, original[p.name], p.get())
            for p in params if p.get() != original[p.name]]


def main() -> None:
    """
    Load games, fit K, run the descent, and report.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--epd', default=DEFAULT_EPD,
                        help='labeled EPD file to fit on (the default input)')
    parser.add_argument('--games-dir', nargs='?', const=DEFAULT_GAMES_DIR,
                        help='fit on our own PGNs instead of --epd; there are '
                             'far too few of them to support a fit, so this is '
                             'kept for comparison only')
    parser.add_argument('--limit', type=int, default=None,
                        help='cap the input: PGN games, or EPD positions '
                             f'(default {DEFAULT_EPD_LIMIT:,} for EPD)')
    parser.add_argument('--rounds', type=int, default=12)
    parser.add_argument('--seed', type=int, default=SPLIT_SEED)
    args = parser.parse_args()

    if args.games_dir:
        print(f'reading games from {args.games_dir}')
        groups = load_positions(args.games_dir, args.limit)
    else:
        if not os.path.exists(args.epd):
            print(f'no EPD file at {args.epd}\n'
                  'fetch it once with:\n'
                  '  curl -Lo ' + args.epd + ' \\\n'
                  '    https://raw.githubusercontent.com/KierenP/'
                  'ChessTrainingSets/master/quiet-labeled.epd',
                  file=sys.stderr)
            raise SystemExit(1)
        print(f'reading positions from {args.epd}')
        groups = load_epd(args.epd, args.limit or DEFAULT_EPD_LIMIT)
    if not groups:
        print('no usable positions found', file=sys.stderr)
        raise SystemExit(1)

    rng = random.Random(args.seed)
    shuffled = groups[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * (1.0 - VALIDATION_FRACTION))

    def flatten(subset: list[Group]) -> tuple[list[GameState], list[float]]:
        positions: list[GameState] = []
        results: list[float] = []
        for group_positions, group_results in subset:
            positions.extend(group_positions)
            results.extend(group_results)
        return positions, results

    train = flatten(shuffled[:cut])
    valid = flatten(shuffled[cut:])
    # A --limit small enough to yield one group puts every position on one
    # side of the cut, and the empty side divides by zero inside
    # `mean_squared_error`. That is exactly what a smoke run asks for, so say
    # what to change instead of raising ZeroDivisionError from three frames in.
    if not train[0] or not valid[0]:
        print(f'only {len(groups)} group(s) — too few to split; raise --limit',
              file=sys.stderr)
        raise SystemExit(1)
    print(f'{len(groups)} groups -> {len(train[0]):,} training positions '
          f'from {cut} groups, {len(valid[0]):,} validation positions '
          f'from {len(shuffled) - cut} groups')
    print(f'{len(train[0]) / max(1, len(parameters())):,.0f} training '
          f'positions per parameter')

    k = fit_k(*train)
    print(f'fitted K = {k:.2f}')

    changed = tune(train, valid, k, args.rounds)
    print()
    if not changed:
        print('no parameter moved: the data does not support a fit')
        return
    print(f'{len(changed)} parameters moved:')
    for name, before, after in changed:
        print(f'  {name:28} {before:6} -> {after:6}   ({after - before:+})')


if __name__ == '__main__':
    main()
