"""
Analyse every finished bot game with Stockfish, automatically, as they arrive.

The manual workflow was: run the bot for a day, stop it, then run
`engine.tools.sf_review` over the whole record directory. That wastes the bot's own
idle time -- between games the machine is doing nothing -- and it means the
analysis only exists when someone is around to start it.

This daemon closes that gap. It watches the record directory, and whenever a
game finishes *and the bot is not currently playing*, it grades that one game
with Stockfish and appends the result to a JSON Lines file outside the repo.
Over a multi-day unattended run the analysis therefore keeps pace with the
games, and the data is waiting rather than needing hours of catch-up.

Two design points matter for an unattended run:

- **It never competes with a live game.** A game in progress means lichess-bot
  has our engine spawned as a subprocess; the watcher waits for that to clear
  before starting Stockfish, and Stockfish runs at low priority besides. The
  whole reason `sf_review` carries a "do not run while the bot is playing"
  warning is that CPU contention degrades the games being recorded.
- **It is resumable and idempotent.** Which games are done is derived from the
  output file itself, so a crash, a reboot, or a second copy of the process
  costs at most one repeated game. There is no separate state file to fall out
  of sync.

What it stores, per game: every move's centipawn loss, accuracy, and the
seconds actually spent on it (Lichess writes `%clk` into the PGN, so the clock
is free), plus the headers. That is deliberately raw -- ACPL, the
inaccuracy/mistake/blunder ladder, accuracy%, per-phase breakdowns and
"did we spend time where we went wrong" are all recoverable from it later
without re-running a single search.

    PYTHONPATH=. uv run --no-project python -m engine.tools.sf_watch
    ... -m engine.tools.sf_watch --once          # drain the backlog and exit
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

from engine import pgn
from engine.board import GameState
from engine.tools.sf_review import (
    BLUNDER, DEFAULT_RECORDS, INACCURACY, MAX_CPL, MISTAKE, OUR_NAME,
    SF_HASH_MB, SF_THREADS, find_stockfish, move_accuracy, white_pov,
    win_percent,
)
from engine.uci_client import EngineClientError, UciEngineClient

# Deliberately outside the repo: this is generated data about a running
# deployment, not source, and it grows without bound.
DEFAULT_OUTPUT = os.path.expanduser(
    '~/.local/share/pycheckmate/game_analysis.jsonl')
DEFAULT_LOG = os.path.expanduser('~/.local/share/pycheckmate/sf_watch.log')

# Records when each engine build was first deployed, so `--since auto` can put
# the cut at the *version change* rather than at process start. Restarting the
# bot must not orphan a game that finished just before the last shutdown.
DEFAULT_CUTS = os.path.expanduser('~/.local/share/pycheckmate/version_cuts.json')

# Depth 14 rather than sf_review's 16: this runs opportunistically between
# games, and finishing a game's analysis before the next one starts matters
# more here than the last increment of precision. Validated against Lichess's
# own published numbers on two games (10.9/46.9 vs 13/49, 17.6/5.2 vs 20/6).
DEFAULT_DEPTH = 14

POLL_SECONDS = 30

# A live game means lichess-bot has spawned our engine. Matching the module
# path is specific enough not to catch this watcher or an editor.
ENGINE_PROCESS_PATTERN = 'engine.uci'

# Wait this long after the bot goes idle before starting: a new game usually
# follows quickly, and being half-way through a Stockfish search when it does
# is exactly what we are avoiding.
IDLE_GRACE_SECONDS = 20

CLOCK_PATTERN = re.compile(r'\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]')


def engine_version() -> str:
    """
    Identify the engine build that is currently deployed.

    Every record carries this. Without it the analysis file silently mixes
    engine versions, and an average over two different engines describes
    neither — the same failure that made the first 43 games uninterpretable,
    one level down. Game PGNs carry no version marker of their own, so it has
    to be stamped at analysis time by a watcher that is started alongside the
    bot it is grading.

    Returns
    -------
    str
        Short git revision, with ``-dirty`` appended when the working tree
        has uncommitted changes; ``'unknown'`` outside a git checkout.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        rev = subprocess.run(['git', '-C', root, 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, timeout=10)
        if rev.returncode != 0:
            return 'unknown'
        dirty = subprocess.run(['git', '-C', root, 'status', '--porcelain'],
                               capture_output=True, text=True, timeout=10)
        suffix = '-dirty' if dirty.stdout.strip() else ''
        return rev.stdout.strip() + suffix
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


def version_cut(version: str, cuts_path: str, record: bool = True) -> float:
    """
    Find when this engine build was first deployed, recording it if new.

    `--since now` would be wrong across restarts: a game that finished
    moments before a `bot down` would be skipped forever by the next
    `bot up`. Anchoring the cut to the *version* instead means a restart
    picks up exactly where it left off, while a new build still starts a
    clean set.

    Parameters
    ----------
    version : str
        Engine build label.
    cuts_path : str
        JSON file mapping version -> first-seen epoch timestamp.
    record : bool, optional
        Whether an unseen version may be written. False for read-only
        queries, which must not invent a deployment that never happened.

    Returns
    -------
    float
        Epoch timestamp from which games belong to this build.
    """
    cuts: dict[str, float] = {}
    if os.path.exists(cuts_path):
        try:
            with open(cuts_path, encoding='utf-8') as handle:
                cuts = json.load(handle)
        except (json.JSONDecodeError, OSError):
            cuts = {}
    if version not in cuts:
        if not record:
            # A query about a version that was never deployed -- typically
            # because the working tree is dirty, so the rev carries a
            # "-dirty" suffix the running watcher does not have. Fall back to
            # the most recent real cut, which is the deployment actually
            # playing. Returning 0.0 instead would put every historical game
            # "in scope" and leave `bot down` waiting on a backlog that is
            # not its to drain.
            return max(cuts.values()) if cuts else 0.0
        cuts[version] = time.time()
        os.makedirs(os.path.dirname(cuts_path) or '.', exist_ok=True)
        with open(cuts_path, 'w', encoding='utf-8') as handle:
            json.dump(cuts, handle, indent=2)
    return cuts[version]


def log(message: str, log_path: str) -> None:
    """
    Append one timestamped line to the watcher's log, and echo it to a terminal.

    `bot up` starts the watcher with stdout already redirected into
    `log_path`, so printing unconditionally wrote every line twice. The
    print is only useful when a human is watching, which is exactly what
    `isatty()` tests.

    Parameters
    ----------
    message : str
        Text to record.
    log_path : str
        File to append to.

    Returns
    -------
    None
    """
    line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {message}'
    if sys.stdout.isatty():
        print(line, flush=True)
    with open(log_path, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def bot_is_playing() -> bool:
    """
    Report whether lichess-bot currently has a game in progress.

    Returns
    -------
    bool
        True when an engine subprocess is alive, which is the cheapest
        reliable proxy for "a game is being played right now".
    """
    try:
        found = subprocess.run(['pgrep', '-f', ENGINE_PROCESS_PATTERN],
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        # Cannot tell -- assume busy. Delaying analysis is harmless; running
        # it during a live game is not.
        return True
    # pgrep matches this process too when it was started with the module path,
    # so discount our own pid.
    pids = {line for line in found.stdout.split() if line != str(os.getpid())}
    return bool(pids)


def already_done(output_path: str) -> set[str]:
    """
    Read back which games have already been analysed.

    Deriving this from the output file rather than a side-car state file is
    what makes the watcher safely restartable: there is nothing that can
    disagree with the data.

    Parameters
    ----------
    output_path : str
        Path to the JSON Lines output.

    Returns
    -------
    set of str
        Basenames of games already present.
    """
    done: set[str] = set()
    if not os.path.exists(output_path):
        return done
    with open(output_path, encoding='utf-8') as handle:
        for line in handle:
            try:
                done.add(json.loads(line)['file'])
            except (json.JSONDecodeError, KeyError):
                continue  # A torn last line from a kill; ignore it.
    return done


def parse_clocks(text: str) -> list[float]:
    """
    Extract the clock reading after each move from a Lichess PGN.

    Parameters
    ----------
    text : str
        Full PGN text.

    Returns
    -------
    list of float
        Seconds remaining after each half-move, in order.
    """
    return [int(h) * 3600 + int(m) * 60 + float(s)
            for h, m, s in CLOCK_PATTERN.findall(text)]


def seconds_spent(clocks: list[float], index: int, increment: float) -> float:
    """
    Work out how long one move actually took.

    A clock reading is what remained *after* the move, so the time spent is
    the drop from the same player's previous reading, with the increment
    (which was added on completion) taken back off.

    Parameters
    ----------
    clocks : list of float
        Per-half-move clock readings.
    index : int
        Half-move index.
    increment : float
        Increment in seconds.

    Returns
    -------
    float
        Seconds spent, or -1.0 when it cannot be determined (the first two
        half-moves have no previous reading for that player).
    """
    if index < 2 or index >= len(clocks):
        return -1.0
    return max(0.0, clocks[index - 2] - clocks[index] + increment)


def analyse(path: str, engine: UciEngineClient, depth: int,
            version: str) -> dict | None:
    """
    Grade one finished game and return everything worth keeping about it.

    Parameters
    ----------
    path : str
        PGN file to analyse.
    engine : UciEngineClient
        A ready Stockfish client.
    depth : int
        Fixed search depth per position.
    version : str
        Engine build that played the game; stamped into the record so an
        analysis file can never silently average two different engines.

    Returns
    -------
    dict or None
        A record ready to serialise, or None when the file is unparseable or
        the bot did not play in it.
    """
    text = open(path, encoding='utf-8', errors='replace').read()

    def header(name: str) -> str:
        found = re.search(rf'\[{name} "([^"]*)"', text)
        return found.group(1) if found else ''

    white, black = header('White'), header('Black')
    if OUR_NAME not in (white, black):
        return None
    ours_white = white == OUR_NAME

    try:
        _fen, sans = pgn.parse_pgn(text)
    except pgn.PgnError:
        return None

    gs = GameState()
    uci_moves: list[str] = []
    movers: list[bool] = []
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

    time_control = header('TimeControl')
    increment = 0.0
    if '+' in time_control:
        try:
            increment = float(time_control.split('+')[1])
        except ValueError:
            increment = 0.0
    clocks = parse_clocks(text)

    # Each position is evaluated once and reused as the "after" of one move
    # and the "before" of the next.
    evals: list[int] = []
    for i in range(len(uci_moves) + 1):
        _best, score, is_mate = engine.analyse(uci_moves[:i], depth)
        evals.append(white_pov(score, is_mate, i % 2 == 0))

    moves = []
    for i, by_white in enumerate(movers):
        swing = evals[i] - evals[i + 1] if by_white else evals[i + 1] - evals[i]
        loss = min(max(swing, 0), MAX_CPL)
        # Win% is always from the point of view of whoever just moved.
        before = win_percent(evals[i] if by_white else -evals[i])
        after = win_percent(evals[i + 1] if by_white else -evals[i + 1])
        moves.append({
            'ply': i,
            'move_number': i // 2 + 1,
            'uci': uci_moves[i],
            'ours': by_white == ours_white,
            'cpl': loss,
            'accuracy': round(move_accuracy(before, after), 2),
            'eval_before': evals[i],
            'eval_after': evals[i + 1],
            'seconds': round(seconds_spent(clocks, i, increment), 2),
        })

    ours = [m for m in moves if m['ours']]
    theirs = [m for m in moves if not m['ours']]

    def summarise(group: list[dict]) -> dict:
        if not group:
            return {}
        losses = [m['cpl'] for m in group]
        return {
            'moves': len(group),
            'acpl': round(sum(losses) / len(losses), 2),
            'accuracy': round(sum(m['accuracy'] for m in group) / len(group), 2),
            'inaccuracies': sum(1 for c in losses if INACCURACY <= c < MISTAKE),
            'mistakes': sum(1 for c in losses if MISTAKE <= c < BLUNDER),
            'blunders': sum(1 for c in losses if c >= BLUNDER),
        }

    return {
        'file': os.path.basename(path),
        'engine_version': version,
        'analysed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'mtime': os.path.getmtime(path),
        'depth': depth,
        'site': header('Site'),
        'white': white,
        'black': black,
        'our_colour': 'w' if ours_white else 'b',
        'opponent': black if ours_white else white,
        'our_elo': header('WhiteElo' if ours_white else 'BlackElo'),
        'opponent_elo': header('BlackElo' if ours_white else 'WhiteElo'),
        'result': header('Result'),
        'time_control': time_control,
        'termination': header('Termination'),
        'opening': header('Opening'),
        'us': summarise(ours),
        'them': summarise(theirs),
        'moves': moves,
    }


def main() -> None:
    """
    Watch the record directory and analyse finished games as they appear.

    Returns
    -------
    None
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', default=DEFAULT_RECORDS)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--log', default=DEFAULT_LOG)
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH)
    parser.add_argument('--poll', type=int, default=POLL_SECONDS)
    parser.add_argument('--once', action='store_true',
                        help='analyse the backlog and exit, ignoring the bot')
    parser.add_argument('--version', default='',
                        help='engine build label (default: current git rev)')
    parser.add_argument('--cuts', default=DEFAULT_CUTS)
    parser.add_argument('--pending', action='store_true',
                        help='print how many games still need analysing, then '
                             'exit. Used by `bot down` to tell whether it is '
                             'safe to stop without losing a record.')
    parser.add_argument('--since', default='',
                        help='ignore games older than this: "auto" (when this '
                             'engine build was first deployed -- the right '
                             'choice for a service), "now", an epoch '
                             'timestamp, or "YYYY-MM-DD HH:MM:SS". Games played '
                             'by an earlier engine must be excluded, not '
                             'averaged in with the current one.')
    args = parser.parse_args()

    version = args.version or engine_version()
    since = 0.0
    if args.since == 'auto':
        # A query must not *create* a cut. `bot down` calls --pending, and
        # registering there would stamp whatever HEAD happened to be at
        # shutdown as a deployed version -- inventing versions that never
        # played a game and muddying the file the analysis depends on.
        since = version_cut(version, args.cuts, record=not args.pending)
    elif args.since == 'now':
        since = time.time()
    elif args.since:
        try:
            since = float(args.since)
        except ValueError:
            since = time.mktime(time.strptime(args.since, '%Y-%m-%d %H:%M:%S'))

    def pending_games() -> list[str]:
        done = already_done(args.output)
        return [p for p in sorted(
                    (os.path.join(args.records, f)
                     for f in os.listdir(args.records) if f.endswith('.pgn')),
                    key=os.path.getmtime)
                if os.path.basename(p) not in done
                and os.path.getmtime(p) >= since]

    if args.pending:
        print(len(pending_games()))
        return

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    os.makedirs(os.path.dirname(args.log) or '.', exist_ok=True)

    cut = (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))
           if since else 'all games')
    log(f'watching {args.records} -> {args.output} '
        f'(depth {args.depth}, version {version}, from {cut})', args.log)

    engine: UciEngineClient | None = None
    try:
        while True:
            pending = pending_games()

            if pending and (args.once or not bot_is_playing()):
                if not args.once:
                    # Let a new game claim the CPU first if one is starting.
                    time.sleep(IDLE_GRACE_SECONDS)
                    if bot_is_playing():
                        continue

                if engine is None:
                    engine = UciEngineClient([find_stockfish()])
                    engine.set_option('Threads', SF_THREADS)
                    engine.set_option('Hash', SF_HASH_MB)

                path = pending[0]
                try:
                    record = analyse(path, engine, args.depth, version)
                except EngineClientError as exc:
                    # Stockfish died; drop the client so the next pass
                    # respawns it, and try this game again then.
                    log(f'engine error on {os.path.basename(path)}: {exc}',
                        args.log)
                    engine = None
                    continue
                except Exception as exc:  # noqa: BLE001 - must not stop the watch
                    log(f'skipping {os.path.basename(path)}: {exc!r}', args.log)
                    record = {'file': os.path.basename(path),
                              'error': repr(exc)}

                if record is None:
                    # Not our game, or unparseable. Record that so it is not
                    # retried on every pass forever.
                    record = {'file': os.path.basename(path), 'skipped': True}

                with open(args.output, 'a', encoding='utf-8') as handle:
                    handle.write(json.dumps(record) + '\n')

                summary = record.get('us') or {}
                log(f'{record["file"][:44]:<44} '
                    f'acpl {summary.get("acpl", "-")} '
                    f'acc {summary.get("accuracy", "-")} '
                    f'blunders {summary.get("blunders", "-")}', args.log)
                continue

            if args.once:
                log('backlog drained', args.log)
                break
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log('stopped', args.log)
    finally:
        if engine is not None:
            engine.close()


if __name__ == '__main__':
    main()
