"""
Grade the bot's real games with Stockfish, not with ourselves.

An engine cannot grade itself. The retired `tm_replay`/`tm_allocate` pair
scored the bot's games with *our own* search, which was useful for relative
before/after comparisons but had a structural blind spot: it could not see a
mistake our engine does not understand. If the engine misjudges a position, it
misjudges it identically when playing and when grading, and the move looks
fine both times.

Stockfish is an independent, far stronger referee, so its verdict is the
one worth trusting about *how well we actually played*. This module drives
a Stockfish subprocess over plain UCI (no new Python dependency — it reuses
`UciEngineClient`), replays every PGN in the bot's game-record directory,
and reports the same numbers Lichess shows: average centipawn loss, and
counts of inaccuracies, mistakes and blunders.

The point is not to admire the numbers but to split them by engine version.
Game records carry no version marker, so games are bucketed by file mtime
against `--cut` — see the `lichess-game-records-version-cut` note for the
timestamps.

    PYTHONPATH=. uv run --no-project python -m engine.tools.sf_review
    ... -m engine.tools.sf_review --depth 16 --cut "2026-07-19 14:45:10"
    ... -m engine.tools.sf_review --limit 2          # quick smoke run

**Do not run a full pass while the bot is playing.** Stockfish will compete
with the live engine for CPU and degrade the very games being collected.
"""
import argparse
import datetime
import math
import glob
import os
import re
import shutil
import sys

from engine import pgn
from engine.board import GameState
from engine.uci_client import EngineClientError, UciEngineClient

# Where lichess-bot drops its PGNs (config.yml: pgn_directory)
DEFAULT_RECORDS = os.path.expanduser(
    '~/PycharmProjects/lichess-bot/game_records')
OUR_NAME = 'PyCheckmate'

# Fixed depth rather than movetime: reproducible across runs and machines.
# 16 is deep enough that the verdict is stable and shallow enough that a
# few hundred positions finish in minutes.
DEFAULT_DEPTH = 16

# One thread keeps a batch run from monopolising the machine; the analysis
# is embarrassingly sequential anyway, so depth matters far more than width.
SF_THREADS = 1
SF_HASH_MB = 256

# A mate is not a centipawn score. Map it to something large but finite so
# arithmetic stays sane.
MATE_SCORE = 10_000

# Evaluations are clamped to +-EVAL_CLAMP *before* differencing. This is the
# detail that makes the numbers agree with Lichess, and it is not cosmetic:
# without it, every move played in an already-lost position keeps scoring
# huge "losses" against a -2000 baseline, so one bad game inflates a whole
# average. Once a position is winning by a rook, the difference between +900
# and +2500 is not a mistake anyone can be charged for.
EVAL_CLAMP = 1_000
MAX_CPL = 1_000

# Lichess's ladder, in centipawns of loss.
INACCURACY = 50
MISTAKE = 100
BLUNDER = 300

# Lichess's win%/accuracy curves, so our numbers line up with the site's.
WIN_PERCENT_SLOPE = 0.00368208
ACCURACY_SCALE = 103.1668
ACCURACY_DECAY = 0.04354
ACCURACY_OFFSET = 3.1669


def find_stockfish() -> str:
    """
    Locate a Stockfish binary.

    Returns
    -------
    str
        Path to the executable.

    Raises
    ------
    SystemExit
        With an install hint if none is found — this module is useless
        without it, and a clear message beats a spawn traceback.
    """
    found = shutil.which('stockfish')
    if found:
        return found
    for path in ('/opt/homebrew/bin/stockfish', '/usr/local/bin/stockfish'):
        if os.path.exists(path):
            return path
    sys.exit('stockfish not found — install it with:  brew install stockfish')


def white_pov(score: int, is_mate: bool, white_to_move: bool) -> int:
    """
    Convert a UCI score into centipawns from White's point of view.

    UCI reports scores from the *side to move's* perspective, which flips
    every ply. Comparing consecutive positions is only meaningful once both
    are expressed in the same frame, so everything is normalised to White.

    Parameters
    ----------
    score : int
        Raw UCI value: centipawns, or moves-to-mate when `is_mate`.
    is_mate : bool
        Whether the score is a mate distance rather than centipawns.
    white_to_move : bool
        Side to move in the position that produced the score.

    Returns
    -------
    int
        Centipawns, positive favouring White, clamped to +-`EVAL_CLAMP`.
    """
    if is_mate:
        # Nearer mates score higher, so a mate in 1 beats a mate in 5.
        value = MATE_SCORE - abs(score)
        value = value if score > 0 else -value
    else:
        value = score
    signed = value if white_to_move else -value
    return min(max(signed, -EVAL_CLAMP), EVAL_CLAMP)


def win_percent(centipawns: int) -> float:
    """
    Convert a centipawn evaluation into an expected-score percentage.

    Centipawns are not linear in *practical* value: the difference between
    +100 and +200 changes a game's outcome far more than the difference
    between +900 and +1000. Lichess maps them through a logistic curve before
    grading, and matching that mapping is what makes our numbers comparable
    with the site's.

    Parameters
    ----------
    centipawns : int
        Evaluation from one side's point of view.

    Returns
    -------
    float
        Expected score for that side, 0-100.
    """
    return 50 + 50 * (2 / (1 + math.exp(-WIN_PERCENT_SLOPE * centipawns)) - 1)


def move_accuracy(before: float, after: float) -> float:
    """
    Grade one move on Lichess's 0-100 accuracy scale.

    Accuracy is a function of how much *winning chance* a move gave away,
    not how many centipawns — so a blunder in an already-lost position costs
    little, which matches how players actually judge moves.

    Parameters
    ----------
    before : float
        Win percentage for the mover before the move.
    after : float
        Win percentage for the mover after it.

    Returns
    -------
    float
        Accuracy for this move, clamped to 0-100.

    Notes
    -----
    Lichess additionally weights each move by the local "volatility" of the
    position and blends a harmonic mean into the game total. This is the
    per-move curve only, so a game average computed from it runs slightly
    higher than the site's for sharp games. Good enough to compare our own
    games against each other, which is what it is for.
    """
    loss = max(0.0, before - after)
    raw = ACCURACY_SCALE * math.exp(-ACCURACY_DECAY * loss) - ACCURACY_OFFSET
    return min(100.0, max(0.0, raw))


def analyse_game(path: str, engine: UciEngineClient, depth: int
                 ) -> tuple[str, list[tuple[bool, int]]] | None:
    """
    Replay one PGN and measure the centipawn loss of every move in it.

    Every position is evaluated exactly once and reused as both the "after"
    of one move and the "before" of the next, which halves the Stockfish
    calls compared with evaluating each move in isolation.

    Parameters
    ----------
    path : str
        Path to the PGN file.
    engine : UciEngineClient
        A ready Stockfish client.
    depth : int
        Fixed search depth per position.

    Returns
    -------
    tuple of (str, list of (bool, int)), or None
        `(our_colour, moves)` where `our_colour` is ``'w'`` or ``'b'`` and
        each move is `(is_ours, centipawn_loss)`. None when the file cannot
        be parsed or the bot did not play in it.
    """
    text = open(path, encoding='utf-8', errors='replace').read()
    white = (re.search(r'\[White "([^"]*)"', text) or [None, ''])[1]
    black = (re.search(r'\[Black "([^"]*)"', text) or [None, ''])[1]
    if OUR_NAME not in (white, black):
        return None
    our_colour = 'w' if white == OUR_NAME else 'b'

    try:
        _fen, sans = pgn.parse_pgn(text)
    except pgn.PgnError:
        return None

    # Replay to UCI notation, which is what the reference engine speaks.
    gs = GameState()
    uci_moves: list[str] = []
    movers: list[bool] = []          # True when White made this move
    for san in sans:
        try:
            move = pgn.san_to_move(gs, san)
        except pgn.PgnError:
            break
        movers.append(gs.white_to_move)
        uci_moves.append(move.get_uci_notation())
        gs.make_move(move, annotate=False)

    if len(uci_moves) < 2:
        return None

    # Evaluate every position once: evals[i] is the position *before* move i.
    evals: list[int] = []
    for i in range(len(uci_moves) + 1):
        _best, score, is_mate = engine.analyse(uci_moves[:i], depth)
        # Side to move alternates from White at the start position.
        evals.append(white_pov(score, is_mate, i % 2 == 0))

    moves: list[tuple[bool, int]] = []
    for i, by_white in enumerate(movers):
        # A move is bad if it moves the White-POV score against its own
        # side. Clamp at zero: "better than Stockfish expected" is not a
        # negative loss, it just means the reference had a different plan.
        swing = evals[i] - evals[i + 1] if by_white else evals[i + 1] - evals[i]
        loss = min(max(swing, 0), MAX_CPL)
        moves.append((by_white == (our_colour == 'w'), loss))
    return our_colour, moves


def summarise(label: str, losses: list[int]) -> None:
    """
    Print Lichess-style accuracy statistics for a set of moves.

    Parameters
    ----------
    label : str
        Row heading.
    losses : list of int
        Per-move centipawn losses.
    """
    if not losses:
        print(f'  {label:<28} (no moves)')
        return
    acpl = sum(losses) / len(losses)
    inacc = sum(1 for c in losses if INACCURACY <= c < MISTAKE)
    mist = sum(1 for c in losses if MISTAKE <= c < BLUNDER)
    blun = sum(1 for c in losses if c >= BLUNDER)
    print(f'  {label:<28} ACPL {acpl:6.1f}   '
          f'inacc {inacc:3d}  mistakes {mist:3d}  blunders {blun:3d}   '
          f'({len(losses)} moves)')


def main() -> None:
    """
    Analyse the game records with Stockfish and report by engine version.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', default=DEFAULT_RECORDS)
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH)
    parser.add_argument('--cut', default='2026-07-19 14:45:10',
                        help='version cut "YYYY-MM-DD HH:MM:SS"; games before '
                             'it are the old engine, from it the new one')
    parser.add_argument('--limit', type=int, default=0,
                        help='analyse only the newest N games (smoke runs)')
    args = parser.parse_args()

    cut = datetime.datetime.strptime(args.cut, '%Y-%m-%d %H:%M:%S').timestamp()
    files = sorted(glob.glob(os.path.join(args.records, '*.pgn')),
                   key=os.path.getmtime)
    if args.limit:
        files = files[-args.limit:]
    if not files:
        sys.exit(f'no PGNs under {args.records}')

    engine = UciEngineClient([find_stockfish()])
    engine.set_option('Threads', SF_THREADS)
    engine.set_option('Hash', SF_HASH_MB)
    print(f'Stockfish depth {args.depth}, {len(files)} game(s), '
          f'cut at {args.cut}\n')

    buckets: dict[str, list[int]] = {'old': [], 'new': [], 'opp': []}
    games = {'old': 0, 'new': 0}
    try:
        for path in files:
            got = analyse_game(path, engine, args.depth)
            if got is None:
                continue
            _colour, moves = got
            era = 'new' if os.path.getmtime(path) >= cut else 'old'
            games[era] += 1
            ours = [loss for is_ours, loss in moves if is_ours]
            theirs = [loss for is_ours, loss in moves if not is_ours]
            buckets[era].extend(ours)
            buckets['opp'].extend(theirs)
            # Per-game ACPL is what Lichess prints, so this line can be
            # checked directly against the site's analysis of the same game.
            print(f'  {os.path.basename(path)[:44]:<44} [{era}] '
                  f'us {sum(ours) / max(len(ours), 1):5.1f}  '
                  f'opp {sum(theirs) / max(len(theirs), 1):5.1f}', flush=True)
    except (EngineClientError, KeyboardInterrupt) as exc:
        print(f'\nstopped early: {exc}')
    finally:
        engine.close()

    print(f'\n=== Stockfish {args.depth}-ply verdict ===')
    summarise(f'us, old TM ({games["old"]} games)', buckets['old'])
    summarise(f'us, new TM ({games["new"]} games)', buckets['new'])
    summarise('opponents (all games)', buckets['opp'])
    print('\nLower ACPL is better. Compare the two "us" rows: that is the '
          'time-management\nchange judged by an engine that has no stake in '
          'our own evaluation being right.')


if __name__ == '__main__':
    main()
