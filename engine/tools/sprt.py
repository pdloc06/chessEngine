"""
Decide whether a change gains Elo, using SPRT instead of a fixed-size match.

The v1 engine judged 5-20 Elo changes with 100-400 game matches, whose
resolution is +-70 / +-35 Elo. That is not a strict test, it is an
*undetectable* one: the answer was buried in the error bar before the first
game was played, which is why a whole program of search tuning returned "~0
net Elo, most of it unresolvable noise". The conclusion was never in the data.

A Sequential Probability Ratio Test fixes the design rather than the sample
size. Two hypotheses are stated up front -- H0 "the change is worth at most
`elo0`" and H1 "it is worth at least `elo1`" -- and after every game the
accumulated evidence is tested against both. The match stops the moment one
is decisively favoured, which means a genuine improvement is confirmed in far
fewer games than a fixed match needs, a genuine regression is caught early,
and a truly neutral change simply runs on without ever producing a fake
verdict. It is the standard method for every serious engine.
(https://www.chessprogramming.org/Sequential_Probability_Ratio_Test)

This module is a thin wrapper over `fastchess`, which implements the actual
tournament and statistics. What it adds is the project-specific setup that is
easy to get subtly wrong:

- both sides run under PyPy when available, from *their own checkout*, so a
  baseline worktree really executes the baseline's code;
- games are played in colour-reversed pairs (``-repeat``) from a shared
  opening, so an unbalanced line is handed to each engine exactly once and
  cancels instead of becoming noise;
- the book is `books/uho_5000.epd`, a seeded 5,000-position sample of
  Stockfish's UHO_Lichess_4852_v1. Deliberately unbalanced human openings
  reduce the draw rate, which raises the information per game. Random N-ply
  walks -- what v1 used -- do neither, and can start a game already lost.

Usage: create a baseline to compare against, then run.

    git worktree add /tmp/baseline master
    PYTHONPATH=. uv run --no-project python -m engine.tools.sprt /tmp/baseline
    ... -m engine.tools.sprt /tmp/baseline --tc 10+0.1 --elo1 5

Read the verdict literally. "H1 accepted" means the change is probably worth
more than `elo1`; "H0 accepted" means it is probably not worth `elo1` --
which is *not* the same as "it is harmful". A change that stalls without a
verdict is neutral in the range tested, and neutral changes that simplify the
code are still worth keeping.
"""
import argparse
import os
import shutil
import subprocess
import sys

from engine.uci_client import PROJECT_ROOT, resolve_engine_command

# The book ships with the repo so a test is reproducible from a clean clone.
DEFAULT_BOOK = os.path.join(PROJECT_ROOT, 'books', 'uho_5000.epd')

# 60+0 is a faithful scale model of the deployed 5+0, and picking it is not
# arbitrary. Two separate things distort a scaled-down clock:
#
# 1. The hoarding tiers. These used to be absolute seconds, so shrinking the
#    clock moved them to a different point in the *game* -- the trap that cost
#    two overnight gates. They are now fractions of the starting clock
#    (`uci.LOW_CLOCK_FRACTION`), so they scale correctly and no longer
#    constrain the choice.
# 2. `MOVE_OVERHEAD` + `CLOCK_RESERVE` (1.15s). These are *genuinely* absolute
#    -- network latency does not shrink with the clock -- so they cannot be
#    made relative, and they eat a growing share of a smaller clock:
#
#        300s -> 0.4%    120s -> 1.0%    60s -> 1.9%    10s -> 11.5%
#
# 60s keeps that under 2% while running games ~5x faster than the deployed
# control. Below ~60s the engine is measurably playing a different game.
DEFAULT_TC = '60+0'

# elo0=0 / elo1=5 asks "is this worth at least 5 Elo?", the customary bounds
# for an incremental change. alpha/beta are the false-positive and
# false-negative rates.
DEFAULT_ELO0 = 0.0
DEFAULT_ELO1 = 5.0
DEFAULT_ALPHA = 0.05
DEFAULT_BETA = 0.05

# Memory, not cores, is what caps this — and getting it wrong does not look
# like a resource problem. At 4 the whole match was unusable: both engines
# overran the 60s clock by 130-210s and forfeited, with the overrun growing as
# each game went on. That reads like a search bug, and it is not one. Each
# engine holds a transposition table of up to `uci.TT_MAX_ENTRIES` (2M) plus
# `eval._EVAL_CACHE` (1M), measured at 460-625 MB per process partway through a
# single 114-ply game and still climbing. Concurrency N runs 2N engines, so 4
# means 8 processes competing for 8 GB: the machine swaps, every search slows
# by two orders of magnitude, and the clock is what notices. The caches grow
# with the game, which is exactly why the overrun did too.
#
# 2 keeps four processes at ~2.5 GB and also matches the CPU guidance in
# CLAUDE.md, which already records that 3 saturates this M2's four performance
# cores. Raise it only on a machine with more RAM, not more cores.
DEFAULT_CONCURRENCY = 2

# A ply cap that no sane game reaches, as a backstop against a bug that makes
# two engines shuffle forever.
MAX_MOVES = 300


def find_fastchess() -> str:
    """
    Locate the fastchess binary.

    Returns
    -------
    str
        Path to the executable.

    Raises
    ------
    SystemExit
        With build instructions if it is missing.
    """
    found = shutil.which('fastchess')
    if found:
        return found
    local = os.path.expanduser('~/.local/bin/fastchess')
    if os.path.exists(local):
        return local
    sys.exit('fastchess not found. Build it with:\n'
             '  git clone --depth 1 https://github.com/Disservin/fastchess\n'
             '  cd fastchess && make -j8 && cp fastchess ~/.local/bin/')


def engine_args(name: str, checkout: str) -> list[str]:
    """
    Build the fastchess arguments that run one checkout as a UCI engine.

    Each side runs `python -m engine.uci` with `dir` set to its own checkout,
    which is what makes a baseline worktree execute the baseline's code
    rather than the working tree's.

    Parameters
    ----------
    name : str
        Label for this engine in the output.
    checkout : str
        Path to the repo checkout to run.

    Returns
    -------
    list of str
        Arguments to append after `-engine`.
    """
    command = resolve_engine_command() or [sys.executable, '-m', 'engine.uci']
    interpreter, module_args = command[0], command[1:]
    return ['-engine', f'cmd={interpreter}', f'name={name}',
            f'dir={checkout}', f'args={" ".join(module_args)}']


def main() -> None:
    """
    Run an SPRT of the working tree against a baseline checkout.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', help='path to the baseline checkout')
    parser.add_argument('--new', default=str(PROJECT_ROOT),
                        help='checkout under test (default: this repo)')
    parser.add_argument('--tc', default=DEFAULT_TC)
    parser.add_argument('--book', default=DEFAULT_BOOK)
    parser.add_argument('--elo0', type=float, default=DEFAULT_ELO0)
    parser.add_argument('--elo1', type=float, default=DEFAULT_ELO1)
    parser.add_argument('--alpha', type=float, default=DEFAULT_ALPHA)
    parser.add_argument('--beta', type=float, default=DEFAULT_BETA)
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument('--rounds', type=int, default=20000,
                        help='upper bound; SPRT normally stops long before')
    parser.add_argument('--pgnout', default='')
    args = parser.parse_args()

    if not os.path.isdir(args.baseline):
        sys.exit(f'baseline checkout not found: {args.baseline}')
    if not os.path.exists(args.book):
        sys.exit(f'opening book not found: {args.book}')

    command = [
        find_fastchess(),
        *engine_args('new', os.path.abspath(args.new)),
        *engine_args('base', os.path.abspath(args.baseline)),
        '-each', f'tc={args.tc}',
        '-openings', f'file={args.book}', 'format=epd', 'order=random',
        # Colour-reversed pairs: the same opening is played twice with the
        # sides swapped, so its built-in advantage cancels.
        '-repeat',
        '-rounds', str(args.rounds), '-games', '2',
        '-sprt', f'elo0={args.elo0}', f'elo1={args.elo1}',
        f'alpha={args.alpha}', f'beta={args.beta}', 'model=normalized',
        '-concurrency', str(args.concurrency),
        '-maxmoves', str(MAX_MOVES),
        '-recover',
    ]
    if args.pgnout:
        command += ['-pgnout', f'file={args.pgnout}']

    print(f'new:      {args.new}\nbaseline: {args.baseline}')
    print(f'tc {args.tc}, SPRT elo0={args.elo0} elo1={args.elo1} '
          f'alpha={args.alpha} beta={args.beta}\n')
    sys.exit(subprocess.call(command))


if __name__ == '__main__':
    main()
