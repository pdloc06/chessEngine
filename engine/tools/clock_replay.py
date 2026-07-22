"""
Replay recorded games' clocks to see where a time-management constant sends time.

`MIN_MOVES_TO_GO` was raised from 18 to 28 to move thinking time out of the
middlegame and into the endgame, where the 101-game analysis found 32% of our
moves and **47% of our blunders** being played on 8-12% of the clock. The
obvious way to check that is to play several hundred more games and compare —
which costs about eight days and answers with an error bar of +-28 Elo.

This tool answers a smaller question much faster, and it is the right question
to ask first: **did the clock actually move where we aimed it?**

That is a *mechanism* measurement rather than a strength measurement, and the
distinction is the whole point. A win rate is one bit per game, so it needs
hundreds of games to say anything. A clock allocation is one number per move —
roughly sixty observations per game — and every game we have ever played is
already recorded with per-move timings in `game_analysis.jsonl` (taken from the
PGN's `%clk` tags). So the allocation can be recomputed offline, for any value
of the constant, over games that have already happened, in about a second.

## What it does

For each of our moves in each recorded game it reconstructs the clock we would
have been looking at:

    remaining = initial + increment * moves_played - seconds spent so far

then asks `uci.clock_move_budget` what it would have granted, under each value
of `MIN_MOVES_TO_GO` you name. Aggregating by game phase gives the same table
the 101-game analysis produced, except computed rather than observed, and
available for constants we never actually played.

## What it cannot tell you

**Whether the reallocation helps.** It shows the clock moved; it does not show
the engine plays better with it. Only games answer that, and this tool exists
precisely because that answer is expensive.

**Anything about the best-move stability early exit.** That gate narrows in
response to how many search iterations agreed on the root move, which is a
property of a search that happened, not of the clock — and the records do not
carry it. Validating that one needs the search instrumented to report when it
fired and how much time it handed back. This tool deliberately does not guess.

Usage
-----
    PYTHONPATH=. uv run --no-project python -m engine.tools.clock_replay
    ... -m engine.tools.clock_replay --settings 18 28 40 --version fd9ebc0
"""
import argparse
import json
import os
import sys
from collections import defaultdict

from engine import uci

DEFAULT_RECORDS = os.path.expanduser(
    '~/.local/share/pycheckmate/game_analysis.jsonl')

# The phase buckets the 101-game analysis used, kept identical so its published
# table and this tool's output can be read side by side. Bounds are our own
# move numbers, inclusive.
PHASES = ((1, 15), (16, 30), (31, 45), (46, 10_000))

# `sf_watch` writes -1.0 for a move whose clock could not be read: the PGN's
# `%clk` tag records the time *remaining after* a move, so the first one has no
# predecessor to subtract from.
NO_CLOCK = -1.0


def parse_time_control(text: str) -> tuple[float, float]:
    """
    Split a PGN time control into initial seconds and increment seconds.

    Parameters
    ----------
    text : str
        A `base+increment` string in seconds, e.g. `'600+2'`.

    Returns
    -------
    tuple of (float, float)
        Initial clock and increment, both in seconds. `(0.0, 0.0)` if the
        string is not a normal time control — correspondence games say `-`,
        and those carry no usable clock.
    """
    base, _, inc = text.partition('+')
    try:
        return float(base), float(inc or 0)
    except ValueError:
        return 0.0, 0.0


def phase_of(move_number: int) -> tuple[int, int]:
    """
    Bucket a move number into its game phase.

    Parameters
    ----------
    move_number : int
        Our own move number, counting from 1.

    Returns
    -------
    tuple of (int, int)
        The matching entry of `PHASES`.
    """
    for low, high in PHASES:
        if low <= move_number <= high:
            return low, high
    return PHASES[-1]


def replay_game(record: dict, min_moves_to_go: int) -> list[tuple[int, float]]:
    """
    Simulate the clock one recorded game would have run under a given constant.

    The clock is **evolved from the budgets being tested**, not reconstructed
    from what the game actually spent, and that distinction is the whole
    instrument. The argument for raising `MIN_MOVES_TO_GO` is a feedback loop:
    a bigger divisor is a thinner slice *now*, but it leaves a larger clock
    alive later, and the larger clock more than repays the thinner slice. Pin
    the trajectory to history and that loop is broken — every setting is then
    measured against the same remaining clock, so a bigger divisor can only
    ever look worse, at every phase, by construction. The first version of this
    tool did exactly that and duly reported that raising the constant *hurt*
    the endgame, which is an artefact of the method and not a finding.

    What history supplies instead is the one thing a simulation cannot invent:
    how long our games really run, and at which time controls.

    Parameters
    ----------
    record : dict
        One line of `game_analysis.jsonl`. Only the time control and the number
        of our moves are used.
    min_moves_to_go : int
        The value of `uci.MIN_MOVES_TO_GO` to evaluate. Patched in around the
        call, because the constant is read inside `moves_to_go` rather than
        passed to it.

    Returns
    -------
    list of tuple
        `(move_number, budget_seconds)` for each of our moves.
    """
    initial, increment = parse_time_control(record.get('time_control', ''))
    if initial <= 0:
        return []
    our_moves = sum(1 for m in record.get('moves', ()) if m.get('ours'))
    if not our_moves:
        return []

    original = uci.MIN_MOVES_TO_GO
    uci.MIN_MOVES_TO_GO = min_moves_to_go
    try:
        out: list[tuple[int, float]] = []
        remaining = initial
        for played in range(our_moves):
            if remaining <= 0:
                break             # flagged; a real game would have ended here
            budget = uci.clock_move_budget(int(remaining * 1000),
                                           int(increment * 1000),
                                           played, initial)
            # The search spends its budget, or whatever is left if that is
            # less. Treating spend as equal to budget is the honest upper
            # bound: the soft-stop gate can return time early, and modelling
            # that would need the stability count the records do not carry.
            spend = min(budget, remaining)
            out.append((played + 1, spend))
            remaining += increment - spend
        return out
    finally:
        uci.MIN_MOVES_TO_GO = original


def actual_spend(record: dict) -> list[tuple[int, float]]:
    """
    Extract what a recorded game really spent per move, as a reality check.

    Parameters
    ----------
    record : dict
        One line of `game_analysis.jsonl`.

    Returns
    -------
    list of tuple
        `(move_number, seconds)` for our moves whose clock could be read.
    """
    out: list[tuple[int, float]] = []
    played = 0
    for move in record.get('moves', ()):
        if not move.get('ours'):
            continue
        played += 1
        seconds = move.get('seconds', NO_CLOCK)
        if seconds != NO_CLOCK:
            out.append((played, seconds))
    return out


def load(path: str, version: str | None) -> list[dict]:
    """
    Read game records, optionally keeping one engine version.

    Parameters
    ----------
    path : str
        Path to `game_analysis.jsonl`.
    version : str or None
        Keep only records whose `engine_version` starts with this. Matching by
        prefix rather than equality is deliberate: older records carry a
        `-dirty` suffix, and `==` silently drops every one of them.

    Returns
    -------
    list of dict
        The matching records.
    """
    records = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue          # a truncated final line is skipped
            if version and not str(record.get('engine_version', '')).startswith(version):
                continue
            records.append(record)
    return records


def main() -> None:
    """
    Report the clock allocation by phase for each requested constant.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', default=DEFAULT_RECORDS)
    parser.add_argument('--settings', type=int, nargs='+', default=[18, 28],
                        help='values of MIN_MOVES_TO_GO to compare '
                             f'(shipped: {uci.MIN_MOVES_TO_GO})')
    parser.add_argument('--version', default='',
                        help='keep only this engine_version prefix; records '
                             'from different engines must not be pooled')
    # Unlike the emergency tiers, MIN_MOVES_TO_GO is an absolute move count
    # rather than a fraction of the clock, so *when* it starts binding depends
    # on how long the game runs -- and game length tracks the time control.
    # Splitting by it is therefore meaningful here, where CLAUDE.md's "do not
    # split per time control" rule (written about the fractional tiers) is not.
    parser.add_argument('--tc', default='',
                        help='keep only this time control, e.g. 300+0')
    args = parser.parse_args()

    if not os.path.exists(args.records):
        sys.exit(f'no records at {args.records}')
    records = load(args.records, args.version or None)
    if args.tc:
        records = [r for r in records if r.get('time_control') == args.tc]
    if not records:
        sys.exit('no matching records')

    versions = {str(r.get('engine_version', '?')) for r in records}
    print(f'{len(records)} games, {len(versions)} engine version(s): '
          f'{", ".join(sorted(versions))}')
    if len(versions) > 1 and not args.version:
        print('WARNING: pooling several engine versions. The clock code may '
              'differ between them, so this mixes instruments -- pass '
              '--version to pick one.')

    # The clock actually spent is a property of the games, not of the setting,
    # so it is collected once and printed as the reality check the computed
    # columns are read against.
    actual: dict[tuple[int, int], float] = defaultdict(float)
    computed: dict[int, dict[tuple[int, int], float]] = {
        setting: defaultdict(float) for setting in args.settings}
    counts: dict[tuple[int, int], int] = defaultdict(int)

    for record in records:
        for move_number, seconds in actual_spend(record):
            phase = phase_of(move_number)
            actual[phase] += seconds
            counts[phase] += 1
    for setting in args.settings:
        for record in records:
            for move_number, budget in replay_game(record, setting):
                computed[setting][phase_of(move_number)] += budget

    if not counts:
        sys.exit('no moves had a reconstructable clock')

    total_actual = sum(actual.values())
    totals = {s: sum(computed[s].values()) for s in args.settings}

    for title, as_share in (('share of the clock', True),
                            ('mean seconds per move', False)):
        header = f'{"phase":>8}{"moves":>8}{"actual":>10}'
        header += ''.join(f'{"MMTG=" + str(s):>12}' for s in args.settings)
        print(f'\n{title}')
        print(header)
        print('-' * len(header))
        for phase in PHASES:
            low, high = phase
            label = f'{low}-{high}' if high < 10_000 else f'{low}+'
            moves = max(1, counts[phase])
            row = f'{label:>8}{counts[phase]:>8}'
            row += (f'{100 * actual[phase] / total_actual:>9.1f}%' if as_share
                    else f'{actual[phase] / moves:>10.2f}')
            for setting in args.settings:
                value = computed[setting][phase]
                row += (f'{100 * value / totals[setting]:>11.1f}%' if as_share
                        else f'{value / moves:>12.2f}')
            print(row)

    print('\n"actual" is what the games really spent, under whatever constant '
          'was live at the time.\nThe MMTG columns simulate the clock forward '
          'from each setting over the same game lengths.')

    endgame = PHASES[-1]
    if len(args.settings) > 1:
        low_setting, high_setting = args.settings[0], args.settings[-1]
        moved = (100 * computed[high_setting][endgame] / totals[high_setting]
                 - 100 * computed[low_setting][endgame] / totals[low_setting])
        print(f'\nmoves 46+ gain {moved:+.1f} percentage points of the clock '
              f'going from MIN_MOVES_TO_GO {low_setting} to {high_setting}.')
        print('This says the time moved, not that moving it helped. Only games '
              'can say that.')


if __name__ == '__main__':
    main()
