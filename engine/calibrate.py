"""
Measure the engine's actual playing strength against a calibrated opponent.

This is the measurement the project spent its whole first life without, and
every wrong conclusion traces back to its absence. The bot's Lichess games
were all *casual*, so its rating never moved off the provisional 3000 — which
in turn told Lichess matchmaking to pair it with the strongest bots on the
site. It lost 42 of 43. That number says nothing about the engine: an 1800
engine and a 2400 engine both lose ~100% against 2900 opposition, so the
score cannot distinguish them, and no amount of analysis downstream can
recover a signal the sample never contained.

Stockfish solves this. With ``UCI_LimitStrength`` it plays at a *requested*
Elo (roughly 1320-3190), which turns it into a ruler rather than an
executioner. Bracketing the level where we score 50% gives a real number,
with a real error bar, in an hour on one machine — no Lichess account, no
rating volatility, no waiting a day for 43 games.

    PYTHONPATH=. uv run --no-project python -m engine.calibrate
    ... -m engine.calibrate --levels 1400,1800 --games 10

Read the output as a bracket, not a rating: with `--games 20` per level the
95% band is roughly +-160 Elo, which is enough to tell 1500 from 2000 and
nowhere near enough to tell 1800 from 1900. That is the correct resolution
for the question being asked here ("what league are we in?").
"""
import argparse
import math
import sys

from engine.selfplay import DEPTH, play_game, random_opening
from engine.sf_review import find_stockfish
from engine.uci_client import UciEngineClient, resolve_engine_command
import random

# Stockfish's UCI_Elo floor. Requesting less is silently clamped, so a level
# below this measures the same opponent as this one.
SF_MIN_ELO = 1320

# Coarse ladder: wide enough to bracket a first-generation engine anywhere
# from "hangs pieces" to "clearly club strength" without wasting games at
# levels the first result already rules out.
DEFAULT_LEVELS = (1320, 1600, 2000, 2400)
DEFAULT_GAMES = 20
MOVETIME = 0.5      # per move, both sides — long enough for a real search
OPENING_PLIES = 8
OPENING_SEED = 20260719


def elo_delta(score: float) -> float:
    """
    Convert a match score into an Elo difference.

    Parameters
    ----------
    score : float
        Points scored divided by games played.

    Returns
    -------
    float
        Elo difference implied by the logistic model, clamped away from the
        infinities at 0 and 1.
    """
    clamped = min(max(score, 1e-3), 1 - 1e-3)
    return -400.0 * math.log10(1.0 / clamped - 1.0)


def spawn_stockfish(elo: int) -> UciEngineClient:
    """
    Start Stockfish pinned to a requested strength.

    Parameters
    ----------
    elo : int
        Target Elo for ``UCI_Elo``. Values below `SF_MIN_ELO` are clamped by
        Stockfish itself.

    Returns
    -------
    UciEngineClient
        A ready client playing at approximately `elo`.
    """
    engine = UciEngineClient([find_stockfish()])
    engine.set_option('Threads', 1)
    engine.set_option('Hash', 64)
    engine.set_option('UCI_LimitStrength', 'true')
    engine.set_option('UCI_Elo', max(elo, SF_MIN_ELO))
    return engine


def play_level(elo: int, games: int, movetime: float) -> tuple[float, int]:
    """
    Play a match against Stockfish at one strength level.

    Colours alternate and each opening line is played twice with the colours
    swapped, so any advantage baked into a random line is handed to each side
    exactly once and cancels instead of becoming noise.

    Parameters
    ----------
    elo : int
        Requested Stockfish strength.
    games : int
        Number of games to play.
    movetime : float
        Per-move budget in seconds, for both sides.

    Returns
    -------
    tuple of (float, int)
        Points scored by our engine, and games actually completed.
    """
    command = resolve_engine_command() or [sys.executable, '-m', 'engine.uci']
    ours = UciEngineClient(command)
    theirs = spawn_stockfish(elo)
    book_rng = random.Random(OPENING_SEED)
    points = 0.0
    played = 0
    opening: list[str] = []
    try:
        for game in range(1, games + 1):
            we_are_white = game % 2 == 1
            if we_are_white:
                opening = random_opening(OPENING_PLIES, book_rng)
            white, black = ((ours, theirs) if we_are_white else (theirs, ours))
            result, plies, failure = play_game(white, black, movetime=movetime,
                                               opening=opening)
            if failure is not None:
                print(f'    game {game}: {failure}')
                continue
            played += 1
            if result in ('checkmate', 'flagged'):
                # The loser is the side to move after `plies` half-moves.
                white_won = plies % 2 == 1
                we_won = white_won == we_are_white
                points += 1.0 if we_won else 0.0
                outcome = 'win' if we_won else 'loss'
            else:
                points += 0.5
                outcome = 'draw'
            print(f'    game {game:>3} ({"W" if we_are_white else "B"}): '
                  f'{result:<14} {plies:>3} plies  {outcome:<4} '
                  f'| {points:.1f}/{played}', flush=True)
    finally:
        ours.close()
        theirs.close()
    return points, played


def main() -> None:
    """
    Run the calibration ladder and report an estimated rating per level.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--levels', default=','.join(str(e) for e in DEFAULT_LEVELS),
                        help='comma-separated Stockfish UCI_Elo levels')
    parser.add_argument('--games', type=int, default=DEFAULT_GAMES)
    parser.add_argument('--movetime', type=float, default=MOVETIME)
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(',')]
    print(f'Calibration: {args.games} games per level at {args.movetime}s/move, '
          f'depth cap {DEPTH}\n')

    results: list[tuple[int, float, int]] = []
    for elo in levels:
        print(f'  vs Stockfish @ {elo}:')
        points, played = play_level(elo, args.games, args.movetime)
        if played:
            results.append((elo, points, played))
        print()

    print('=== strength estimate ===')
    for elo, points, played in results:
        score = points / played
        sigma = math.sqrt(max(score * (1 - score), 1e-6) / played)
        est = elo + elo_delta(score)
        low = elo + elo_delta(max(score - 2 * sigma, 1e-3))
        high = elo + elo_delta(min(score + 2 * sigma, 1 - 1e-3))
        print(f'  vs {elo:>5}: {points:5.1f}/{played:<3} ({score:5.1%})  '
              f'-> ~{est:.0f} Elo (95% {low:.0f} to {high:.0f})')
    print('\nThe levels where the score is not 0% or 100% are the informative '
          'ones;\na shutout only proves the answer lies beyond that level.')


if __name__ == '__main__':
    main()
