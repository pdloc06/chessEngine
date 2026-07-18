"""
A/B strength test: two engine *versions* play a match, and the score becomes Elo.

`engine/selfplay.py` answers "does the engine survive whole games?" — this
module answers the harder question every improvement must face: "is the new
version actually *stronger*?" Intuition is a terrible judge of that. Chess
engines gain strength in increments of tens of Elo points, far below what a
human can feel from watching a few games, so the only honest referee is a
match of many games and a little statistics.

How the measurement works:

- **Two checkouts, one board.** Engine A is normally your working tree and
  engine B a pristine baseline (e.g. ``git worktree add /tmp/baseline master``).
  Each runs as its own UCI subprocess from its own directory — `engine/` is
  pure stdlib, so a bare checkout needs no venv. The referee (`selfplay.play_game`)
  holds the authoritative `GameState` and rejects illegal moves, so a buggy
  "improvement" fails loudly instead of winning by confusion.
- **Colors alternate** every game. White moves first and wins more often at
  every level of chess; without alternation a lopsided color draw would
  masquerade as a strength difference.
- **Score → Elo.** A match score ``s`` (wins + half the draws, as a fraction)
  maps to an Elo difference via the logistic model ``elo = -400·log10(1/s − 1)``:
  55% ≈ +35 Elo, 60% ≈ +70. The mapping is steep near 50%, which is exactly
  where small samples lie — hence the error bar.
- **The ±2σ error bar is the point.** With n games the score's standard error
  is ``sqrt(s(1−s)/n)``; we report the Elo interval two of those wide. If the
  interval straddles 0, the match has *not* shown the change helps — 100 games
  can only resolve differences of roughly ±70 Elo. Keep changes that measure
  positive or that simplify the code while measuring neutral; revert clear
  regressions.

Winner detection needs no help from the engines: `play_game` reports
``('checkmate', plies, None)`` and the mated side is simply the side to move
after `plies` half-moves — even plies means White is to move and thus mated.
Everything else ('draw/stalemate', 'move-cap') counts half a point each.

Run it from the repo root (PyPy is picked up automatically when installed):

    uv run --no-project python -m engine.abtest /tmp/baseline              # A = this tree, B = baseline, 100 games
    uv run --no-project python -m engine.abtest /tmp/baseline 200 0.1     # more games, faster moves
    uv run --no-project python -m engine.abtest . /tmp/baseline 100 0.2   # explicit A and B
    uv run --no-project python -m engine.abtest /tmp/baseline 100 60+0.6  # clock mode: 60s + 0.6s/move

Each positional argument that names a directory is an engine spec; the first
one or two arguments are specs (one spec means "A is this repo"), the
remaining arguments are ``games`` and a time spec. A plain number is a fixed
per-move budget; ``base+inc`` switches on the simulated game clock (see
`selfplay.play_game`), which is the mode that can *measure time management*:
with a fixed movetime, banked time evaporates between moves and any
clock-handling improvement is invisible by construction. Flagged games count
as losses for the side that ran out, exactly like online.
"""
import math
import sys

from engine.selfplay import DEPTH, play_game
from engine.uci_client import PROJECT_ROOT, UciEngineClient, resolve_engine_command

DEFAULT_GAMES = 100
DEFAULT_MOVETIME = 0.2  # seconds per move: fast enough for 100 games in ~an hour


def elo_from_score(score: float) -> float:
    """
    Convert a match score fraction into an Elo difference.

    Parameters
    ----------
    score : float
        Points scored divided by games played, in (0, 1).

    Returns
    -------
    float
        The Elo difference implied by the logistic rating model. Clamped
        input keeps a perfect score from dividing by zero.
    """
    clamped = min(max(score, 1e-3), 1 - 1e-3)
    return -400.0 * math.log10(1.0 / clamped - 1.0)


def spawn(spec: str) -> UciEngineClient:
    """
    Spawn one engine version from a checkout directory.

    Parameters
    ----------
    spec : str
        Path to a repo checkout; the engine subprocess runs `-m engine.uci`
        with this directory as its working directory, so each side executes
        its own copy of the code.

    Returns
    -------
    UciEngineClient
        A ready engine client for that checkout.
    """
    command = resolve_engine_command() or [sys.executable, '-m', 'engine.uci']
    return UciEngineClient(command, cwd=spec)


def main() -> None:
    """
    Play the A-vs-B match and print the score, Elo estimate, and error bar.

    Returns
    -------
    None
    """
    import os

    args = sys.argv[1:]
    specs: list[str] = []
    while args and os.path.isdir(args[0]) and len(specs) < 2:
        specs.append(os.path.abspath(args[0]))
        args = args[1:]
    if not specs:
        sys.exit('usage: python -m engine.abtest [dir_a] <dir_b> [games] [movetime]')
    if len(specs) == 1:
        specs.insert(0, str(PROJECT_ROOT))  # A defaults to this checkout
    games = int(args[0]) if len(args) > 0 else DEFAULT_GAMES
    movetime = DEFAULT_MOVETIME
    clock: tuple[float, float] | None = None
    if len(args) > 1:
        if '+' in args[1]:
            base, inc = args[1].split('+', 1)
            clock = (float(base), float(inc))
        else:
            movetime = float(args[1])

    dir_a, dir_b = specs
    print(f'A: {dir_a}\nB: {dir_b}')
    timing = (f'clock {clock[0]:g}s+{clock[1]:g}s' if clock else f'{movetime}s/move')
    print(f'{games} games at {timing} (depth cap {DEPTH}), colors alternate\n')

    engine_a, engine_b = spawn(dir_a), spawn(dir_b)
    points_a = 0.0
    wins_a = wins_b = draws = 0
    try:
        for game in range(1, games + 1):
            a_is_white = game % 2 == 1
            white, black = (engine_a, engine_b) if a_is_white else (engine_b, engine_a)
            result, plies, failure = play_game(white, black, movetime=movetime, clock=clock)
            if failure is not None:
                sys.exit(f'game {game}: {failure}')

            if result in ('checkmate', 'flagged'):
                # Same parity rule for both: the loser is the side to move
                # after `plies` half-moves (mated, or out of time).
                white_won = plies % 2 == 1
                a_won = white_won == a_is_white
                points_a += 1.0 if a_won else 0.0
                wins_a += a_won
                wins_b += not a_won
                outcome = 'A wins' if a_won else 'B wins'
            else:
                points_a += 0.5
                draws += 1
                outcome = 'draw'
            score = points_a / game
            print(f'game {game:>3} ({"A" if a_is_white else "B"} white): '
                  f'{result:<14} {plies:>3} plies  {outcome:<7} '
                  f'| A {points_a:.1f}/{game} ({score:.1%})', flush=True)
    finally:
        engine_a.close()
        engine_b.close()

    score = points_a / games
    sigma = math.sqrt(score * (1 - score) / games)
    low, high = elo_from_score(score - 2 * sigma), elo_from_score(score + 2 * sigma)
    print(f'\nA: +{wins_a} ={draws} -{wins_b}  ->  {score:.1%}, '
          f'Elo {elo_from_score(score):+.0f} (95% range {low:+.0f} to {high:+.0f})')
    verdict = ('A is stronger' if low > 0
               else 'B is stronger' if high < 0
               else 'no significant difference at this sample size')
    print(verdict)


if __name__ == '__main__':
    main()
