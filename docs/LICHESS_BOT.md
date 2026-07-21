# PyCheckmate as a Lichess Bot

Reference for the deployed bot: how the pieces fit together, how to run and
stop it, and the operational traps that have already bitten once. The bot is
live as [@PyCheckmate](https://lichess.org/@/PyCheckmate) — this is not a
roadmap, it's the manual.

---

## How it fits together

Lichess exposes a **Bot API** (a subset of the Board API): a program logs in
with a personal API token, listens to an event stream over HTTPS long-polling,
accepts challenges, and posts moves in **UCI coordinate notation** (`e2e4`,
`e7e8q`). You never implement that protocol by hand — the official
[`lichess-bot`](https://github.com/lichess-bot-devs/lichess-bot) bridge does all
of it and talks to your engine over the **UCI protocol** (stdin/stdout text).

```
Lichess servers  <—HTTPS/JSON—>  lichess-bot (Python bridge)  <—UCI text—>  engine/uci.py  —>  engine/search.py + engine/eval.py + engine/board.py
```

The engine side of that contract is four things, all in `engine/`:

| Piece | Role |
|---|---|
| `GameState.from_fen()` / `to_fen()` | positions arrive as FEN |
| `Move.get_uci_notation()` | moves leave as UCI strings |
| `Move.from_ai_tuple()` | converts search output to a full move |
| Zobrist keys + repetition tracking | so the bot neither blunders into nor misses threefold draws |

`engine/uci.py` implements the command set the bridge needs — `uci`, `isready`,
`ucinewgame`, `position startpos|fen … moves …`, `go depth/movetime/wtime…`,
`quit`. Drive it by hand to check it:

```
$ python -m engine.uci
uci
position startpos moves e2e4 e7e5
go depth 4
bestmove g1f3
quit
```

## Engine-side features that exist for the bot

- **Clock-aware time management** — `handle_go` parses the clock fields
  (`wtime`/`btime`/`winc`/`binc`) Lichess sends on every `go` and turns them
  into a per-move budget via `clock_move_budget()`, letting iterative
  deepening's timer end the search rather than a depth cap. Explicit
  `go depth N` / `go movetime MS` still win. Covered by `tests/test_uci.py`.
- **Persistent transposition table** — `engine/uci.py` holds one table for a
  whole game (`find_best_move(..., tt=...)`), so each move starts warm from the
  previous searches; cleared on `ucinewgame`, capped at `TT_MAX_ENTRIES`.
- **PyPy hosting** — the engine is pure stdlib specifically so it can run under
  PyPy (~2x faster than CPython; the JIT stays warm across a game). Never
  import pygame from `engine/`.
- **Dead-draw detection** — `_insufficient_material` scores K vs K, lone minor,
  and KNN vs K as exact draws, so the bridge's draw-offer handling behaves.
- **Opening book** — bridge-side only, zero engine work: lichess-bot reads a
  polyglot `.bin` (`engines/komodo.bin`) under `engine.polyglot` in `config.yml`.

## Deployment (macOS / Apple Silicon)

The bot only needs outbound HTTPS, so any always-on box works. This one runs on
a **MacBook Air (M2) that travels**, which drives every choice below: the
recipe is macOS-native and **manually controlled**.

### The laptop-sleep problem

The real risk isn't crashes — lichess-bot reconnects through network drops —
it's **sleep**. Screen saver, display sleep and screen lock are all harmless;
background processes keep running and the connection stays up. Only *full
system sleep* suspends the process and drops the game, which then flags or
aborts.

`caffeinate -s` fixes it: it asserts "don't system-sleep" **only while the
wrapped process runs**, and **only on AC power**. So keep the Mac plugged in;
the lid may stay open and the screen is free to sleep. Nothing persistent
changes — unlike `sudo pmset -c disablesleep 1` there is no global setting to
remember to undo.

### The `bot` control script

Deployment used to be a launchd LaunchAgent (`RunAtLoad` + `KeepAlive`), and
that design fought the way this bot is actually hosted. Auto-start at login
opened the event stream on whatever Wi-Fi the laptop had joined, and
`KeepAlive` relaunched a crashing bot in a tight loop — each relaunch
re-opening the stream, which is exactly what the rate limiter punishes. launchd
was retired in favour of a plain script.

`bot` in this repo is that script. Copy it into the lichess-bot clone (it
resolves paths relative to its own location), make it executable, and optionally
put it on your PATH:

```
cp bot <lichess-bot clone>/bot && chmod +x <lichess-bot clone>/bot
ln -sf <lichess-bot clone>/bot ~/.local/bin/bot
```

Four subcommands, pidfile-based (`bot.pid`), all logging to `bot.log`:

```
bot up       # start (wrapped in caffeinate -s), refuses if already running
bot down     # stop, then sweep every process from the bot's venv
bot status   # running/stopped, process count, recent rate-limit lines, log tail
bot log      # tail -f bot.log
```

`bot up` wraps the bot in `caffeinate -s` via `nohup`, so it survives closing
the terminal. There is deliberately **no auto-restart**: a crashed bot stays
down until you say otherwise, which is what keeps the rate limiter happy.
Stopping is the whole cleanup — `caffeinate` dies with the bot and the Mac
resumes normal sleep.

The critical part is the **sweep** in `bot down` (and defensively in `bot up`):
it doesn't just kill the main pid, it `pkill`s everything running the clone's
`.venv` python. The next section is why.

## The rate-limit trap: orphaned children, not restarts

Lichess protects `/api/stream/event` (the connection the bot holds open for
challenges and game events) with an **anti-polling rate limit**. Open it too
often in a short window and Lichess returns 429s; per the
[official API tips](https://lichess.org/page/api-tips) the only cure is to wait
a full minute before touching the API again. Symptoms: the bot shows online but
"accepts challenges without playing" (the stream is down exactly when a game
needs its first move), and the log fills with
`RateLimitedError: /api/stream/event is rate-limited`.

The mechanism took a while to diagnose, and the obvious suspect was wrong.
Restart bursts were blamed first; the actual culprit was **orphaned child
processes**. lichess-bot runs its event-stream watcher (`watch_control_stream`)
as a `multiprocessing.Process`. Kill the main process without a clean shutdown
and that child survives, reparented to PID 1 — invisible unless you go looking —
with its reconnect loop re-opening `/api/stream/event` *forever*. From Lichess's
side the token never went quiet, so the 429 penalty renewed no matter how long
the human "waited". Observed worst case: six orphans quietly hammering the API
for 16 hours while every fresh start hit 429s within seconds.

Diagnose with:

```
ps aux | grep "lichess-bot/.venv"
```

That is why `bot down`'s sweep kills **every** process from the clone's
`.venv`, not just the pidfile pid — and why `bot up` runs the same sweep first
(then waits 60 s, honouring the official cooldown) if it finds strays.

Two smaller mitigations live in the lichess-bot clone as local patches:

- `watch_control_stream` catches `RateLimitedError` and sleeps out the *whole*
  remaining cooldown instead of retrying every second — one reconnect per
  cooldown instead of sixty tracebacks a minute in the log.
- `handle_challenge` applies exponential backoff (60 s → 600 s cap) to 429s on
  the challenge endpoint.

**Day-to-day rule:** batch config changes and spend exactly **one**
`bot down` / `bot up` cycle on them, never a burst — every stream re-open counts
against the anti-polling window. If `bot status` shows rate-limit lines two
minutes after a start, run `bot down`, confirm with `bot status` that **zero**
processes remain (that's the lesson), wait a couple of minutes, and `bot up`
once.

## Bridge configuration

Config lives in the clone's `config.yml` (kept out of git; the token is read
from a `.token` file by `run.sh` rather than stored in the config). Current
shape:

- **Casual only**, standard variant, `blitz`/`rapid`/`classical`.
- **Bullet is off on purpose**: 2 s `move_overhead` plus Python and API latency
  makes flagging a real risk. Blitz is safe — `clock_move_budget()` scales the
  per-move budget to the clock.
- **Matchmaking on** (`allow_matchmaking: true`): the bridge pulls the live
  online-bot list from `/api/bot/online`, filters by rating and variant, and
  after `challenge_timeout` idle minutes challenges one at random using a time
  control drawn from `challenge_initial_time` / `challenge_increment` —
  currently 300 s / 600 s with 0 s / 2 s increment. `accept_bot: true` lets bots
  challenge back.

  **`challenge_timeout` is 2, lowered from 5 on 2026-07-22.** The old value
  left the machine idle for most of the gap between games: measured across 101
  games, the median game ran 13.2 min and the median idle gap 6.2 min, a 55%
  duty cycle. The floor is set by `sf_watch`, which grades each finished game
  with Stockfish in that gap — game-end to analysis-done measured 0.42-1.42
  min, median 0.69, and `sf_watch` only checks `bot_is_playing()` *before* it
  picks up a game, so an analysis already in flight is not interrupted. At
  `challenge_timeout: 1` the next challenge would fire while Stockfish still
  held the CPU in 2% of games, putting a full-strength analysis inside a live
  rated game and corrupting the very records the run exists to produce. At 2
  that is zero, with 0.58 min of headroom. lichess-bot clamps this to a minimum
  of 1 anyway (`lib/config.py`), so lower would need patching the bridge.
- `ponder: false`; `uci_options` commented out (the adapter declares none).

## Testing pipeline

Before anything goes online:

1. `uv run pytest tests/ -q` — engine correctness (perft, Zobrist, search sanity).
2. `uv run --no-project python -m engine.tools.selfplay` — spawns two `engine.uci`
   processes and plays 20 full games refereed by a `GameState`, exiting non-zero
   on any illegal move or crash. This is the integration coverage perft can't
   give: UCI round-trip, search, make/unmake and game-end detection over
   complete games.
3. `lichess-bot` in casual-only mode against your own human account.
4. Only then open the challenge gate to the public and to rated games.

## Learning from the games

lichess-bot writes PGNs to `game_records/` in the clone. Skim losses for
blunders and feed concrete positions back into `tests/` as regression FENs via
`GameState.from_fen`. `engine/analysis.py` grades a whole game
(`blunder`/`mistake`/`missed_win`/…), which makes those PGNs a labelled dataset
rather than just a pile of games.
