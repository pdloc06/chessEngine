"""
Did the engine spend its clock where the game was actually decided?

This is the primary instrument for the time-management work. The premise is
that "better time management" is not a matter of taste: the bot has already
played 16 rated-strength games online, every one of them recorded with `%clk`
annotations, so the *actual* seconds it spent on every move are on disk. And
`engine.analysis` can grade every one of those moves after the fact, which
labels exactly which positions were the ones that mattered.

Put those together and the question becomes measurable without playing a
single new game:

    do the moves the engine got badly wrong get more thinking time
    than the moves it was always going to get right?

If good moves and blunders receive the same number of seconds, the engine is
not managing its clock at all — it is just dividing it up. That is the
hypothesis this script tests, and the baseline any change has to beat.

Run from the repo root:

    uv run --no-project python -m engine.tm_replay [games_dir] [seconds_per_position]
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

from engine import analysis, pgn
from engine.chess_engine import GameState
from engine.move_finder import MoveTuple

BOT_NAME = 'PyCheckmate'
DEFAULT_DIR = Path.home() / 'PycharmProjects' / 'lichess-bot' / 'game_records'

# Grades that mark a position where the game actually turned. MISS is
# included deliberately: failing to convert a won position is a
# time-management failure as much as hanging a piece is.
CRITICAL = {analysis.BLUNDER, analysis.MISTAKE, analysis.MISS}

_TAG = re.compile(r'^\[(\w+)\s+"(.*)"\]')
_CLK = re.compile(r'%clk\s+(\d+):(\d+):(\d+)')


def parse_headers(text: str) -> dict[str, str]:
    """Collect the PGN tag pairs into a dict."""
    headers = {}
    for line in text.splitlines():
        tag = _TAG.match(line)
        if tag:
            headers[tag.group(1)] = tag.group(2)
    return headers


def parse_clocks(text: str) -> list[float]:
    """Read the `%clk` annotations, in ply order, as seconds remaining."""
    return [int(h) * 3600 + int(m) * 60 + int(s)
            for h, m, s in _CLK.findall(text)]


def parse_time_control(tc: str) -> tuple[float, float]:
    """Split a `300+2`-style TimeControl header into (base, increment)."""
    base, _, inc = tc.partition('+')
    return float(base), float(inc or 0)


def analyse_game(path: Path, seconds: float) -> list[dict] | None:
    """
    Replay one game and pair each of the bot's moves with its grade.

    Every position along the mainline is searched once, which gives the
    "before" and "after" evaluations that `analysis.classify_move` needs
    (the position after our move is simply the next position in the list).

    Returns
    -------
    list of dict, or None
        One record per move the bot played, or None when the file is not one
        of the bot's games or carries no clock annotations.
    """
    text = path.read_text()
    headers = parse_headers(text)
    if BOT_NAME not in (headers.get('White', ''), headers.get('Black', '')):
        return None

    bot_is_white = headers.get('White') == BOT_NAME
    base, increment = parse_time_control(headers.get('TimeControl', '300+0'))

    _fen, sans = pgn.parse_pgn(text)
    clocks = parse_clocks(text)
    if len(clocks) != len(sans):
        return None  # annotations don't line up; don't guess

    # Replay once, collecting the position before every ply.
    gs = GameState()
    states: list[str] = []
    moves: list[MoveTuple] = []
    for san in sans:
        states.append(gs.to_fen())
        try:
            played = pgn.san_to_move(gs, san)
        except pgn.PgnError:
            return None
        moves.append(played.to_ai_tuple())
        gs.make_move(played, annotate=False)
    states.append(gs.to_fen())  # final position

    # One search per position: evals[i] describes states[i].
    evals: list[analysis.PositionEval | None] = []
    for fen in states:
        pos = GameState.from_fen(fen)
        if not pos.get_valid_moves(for_ai=True):
            evals.append(None)  # terminal, nothing to search
            continue
        evals.append(analysis.evaluate_position(pos, max_depth=8, time_limit=seconds))

    records = []
    for ply, (san, move) in enumerate(zip(sans, moves)):
        if (ply % 2 == 0) != bot_is_white:
            continue  # opponent's move
        before, after = evals[ply], evals[ply + 1]
        if before is None or after is None:
            continue

        # Time actually spent = clock before this move minus clock after,
        # plus the increment that was added on completing it.
        prev_clock = clocks[ply - 2] if ply >= 2 else base
        spent = max(0.0, prev_clock - clocks[ply] + increment)

        pos = GameState.from_fen(states[ply])
        grade = analysis.classify_move(
            move, before, after,
            white_moved=bot_is_white,
            in_book=analysis.is_book_position(pos),
            sacrifice=analysis.is_sacrifice(pos, move),
        )
        records.append({
            'game': path.stem, 'ply': ply, 'san': san, 'grade': grade,
            'spent': spent, 'clock_after': clocks[ply],
            'base': base, 'increment': increment,
        })
    return records


def main() -> None:
    """Analyse every game in the directory and print the timing report."""
    games_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 0.3

    files = sorted(games_dir.glob('*.pgn'))
    print(f'{len(files)} PGNs in {games_dir}, {seconds}s per position\n')

    all_records: list[dict] = []
    for path in files:
        records = analyse_game(path, seconds)
        if records is None:
            print(f'  skipped {path.stem}')
            continue
        all_records.extend(records)
        leftover = records[-1]['clock_after']
        print(f'  {path.stem[:52]:52} {len(records):3d} moves, '
              f'{leftover:5.0f}s left of {records[-1]["base"]:.0f}s')

    if not all_records:
        print('\nno usable games')
        return

    by_grade: dict[str, list[float]] = defaultdict(list)
    for rec in all_records:
        by_grade[rec['grade']].append(rec['spent'])

    print(f'\n=== time spent per move, by grade ({len(all_records)} moves) ===')
    print(f'{"grade":14} {"n":>4} {"mean":>8} {"median":>8}')
    ladder = [analysis.BEST, analysis.EXCELLENT, analysis.GOOD, analysis.BOOK,
              analysis.FORCED, analysis.INACCURACY, analysis.MISTAKE,
              analysis.MISS, analysis.BLUNDER, analysis.BRILLIANT,
              analysis.GREAT]
    for grade in ladder:
        times = sorted(by_grade.get(grade, []))
        if not times:
            continue
        print(f'{grade:14} {len(times):4d} {sum(times) / len(times):7.1f}s '
              f'{times[len(times) // 2]:7.1f}s')

    # Where in the game do things actually go wrong? Time is worth spending
    # where the mistakes are, so this histogram is what any new allocation
    # curve should be aimed at.
    print('\n=== when the bot goes wrong (own move number) ===')
    print(f'{"moves":10} {"our moves":>10} {"critical":>9} {"rate":>7} {"mean time":>10}')
    buckets = ((1, 10), (11, 20), (21, 30), (31, 40), (41, 60), (61, 200))
    for lo, hi in buckets:
        band = [r for r in all_records if lo <= r['ply'] // 2 + 1 <= hi]
        if not band:
            continue
        crit = [r for r in band if r['grade'] in CRITICAL]
        mean = sum(r['spent'] for r in band) / len(band)
        print(f'{f"{lo}-{hi}":10} {len(band):10d} {len(crit):9d} '
              f'{100 * len(crit) / len(band):6.0f}% {mean:9.1f}s')

    critical = [r['spent'] for r in all_records if r['grade'] in CRITICAL]
    routine = [r['spent'] for r in all_records
               if r['grade'] not in CRITICAL and r['grade'] != analysis.BOOK]

    print('\n=== the question ===')
    if critical and routine:
        c = sum(critical) / len(critical)
        r = sum(routine) / len(routine)
        print(f'critical moves (blunder/mistake/missed win): {c:5.1f}s  (n={len(critical)})')
        print(f'routine moves  (everything else, no book)  : {r:5.1f}s  (n={len(routine)})')
        print(f'ratio critical/routine                     : {c / r:5.2f}x')
        print('  1.00x means the clock is divided, not managed.')

    # Unspent clock is the other half of the story: time banked and never
    # used is time that could have prevented one of those blunders. Split by
    # game length, because a game that ended on move 26 was always going to
    # leave time on the clock — the honest number is the one from long games.
    finals: dict[str, tuple[float, float, int]] = {}
    for rec in all_records:
        left, base, count = finals.get(rec['game'], (0.0, 0.0, 0))
        finals[rec['game']] = (rec['clock_after'], rec['base'], count + 1)

    print('\n=== clock left unspent at game end ===')
    for label, keep in (('short games (<40 of our moves)', lambda n: n < 40),
                        ('long games  (>=40 of our moves)', lambda n: n >= 40)):
        pcts = [100 * left / base for left, base, n in finals.values() if keep(n)]
        if pcts:
            print(f'{label:32} {sum(pcts) / len(pcts):3.0f}% average, '
                  f'{min(pcts):.0f}%-{max(pcts):.0f}% range  (n={len(pcts)})')
    allpct = [100 * left / base for left, base, _ in finals.values()]
    print(f'{"all games":32} {sum(allpct) / len(allpct):3.0f}% average')


if __name__ == '__main__':
    main()
