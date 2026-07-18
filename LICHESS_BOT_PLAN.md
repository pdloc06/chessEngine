# Plan: Running PyCheckmate as a Lichess Bot

This is the roadmap for taking the engine in this repository online as a
`BOT`-flagged account on lichess.org. Steps 1–2 are already implemented in
code; the rest is operational work.

---

## How Lichess bots work (background)

Lichess exposes a **Bot API** (a subset of the Board API): your program logs
in with a personal API token, listens to an event stream over HTTPS
long-polling, accepts challenges, and posts moves in **UCI coordinate
notation** (`e2e4`, `e7e8q`). You never implement the protocol by hand in
practice — the official **`lichess-bot`** bridge (github.com/lichess-bot-devs/lichess-bot)
does all of it and simply talks to your engine over the **UCI protocol**
(stdin/stdout text commands).

So the integration chain is:

```
Lichess servers  <—HTTPS/JSON—>  lichess-bot (Python bridge)  <—UCI text—>  engine/uci.py  —>  engine/move_finder.py + engine/chess_engine.py
```

## Step 1 — Engine prerequisites (DONE in this repo)

The engine must be able to reconstruct any position the server describes and
speak UCI move notation:

- `GameState.from_fen()` / `to_fen()` — positions arrive as FEN (`engine/chess_engine.py`)
- `Move.get_uci_notation()` — moves leave as UCI strings (`engine/chess_engine.py`)
- `Move.from_ai_tuple()` — converts search output to full moves (`engine/chess_engine.py`)
- Zobrist keys + repetition awareness in search (`engine/move_finder.py`) so the bot
  doesn't blunder into (or miss) threefold repetition draws online

## Step 2 — UCI adapter (DONE: `engine/uci.py`)

`engine/uci.py` implements the minimal command set lichess-bot needs:
`uci`, `isready`, `ucinewgame`, `position startpos|fen ... moves ...`,
`go depth/movetime`, `quit`.

Verify locally:

```
$ python -m engine.uci
uci
position startpos moves e2e4 e7e5
go depth 4
bestmove g1f3
quit
```

## Step 3 — Create the bot account

1. Register a **fresh** Lichess account (an account that has ever played a
   rated human game cannot be converted to a bot).
2. Create a personal API token at lichess.org/account/oauth/token with the
   `bot:play` scope. Store it in an environment variable — never commit it.
3. Upgrade the account to a bot (one-time, irreversible):
   `curl -d '' https://lichess.org/api/bot/account/upgrade -H "Authorization: Bearer <TOKEN>"`

## Step 4 — Wire up lichess-bot

1. Clone `https://github.com/lichess-bot-devs/lichess-bot`, install its
   requirements in a separate venv.
2. In its `config.yml`:
   - `token`: your `bot:play` token (or use the env-var indirection).
   - `engine.dir`: path to this repo; `engine.name`: a small launcher script
     (see below); `engine.protocol: uci`.
   - `challenge`: start restrictive — accept only `casual`, variants
     `standard`, time controls `rapid`/`classical` while testing.
3. Launcher script (`engine.sh`, committed here as a template):
   `#!/bin/sh` + `cd /path/to/repo && exec uv run --no-project -p pypy3.11 python -m engine.uci`
   (lichess-bot expects an executable, not a Python module).
4. Run `python lichess-bot.py -v`, then challenge your bot from your own
   account and play a game.

## Step 5 — Time management (DONE: `parse_go_limits` in `engine/uci.py`)

`handle_go` now parses the clock fields (`wtime`, `btime`, `winc`, `binc`)
that Lichess sends on every `go`:

- budget per move = `remaining_ms / 30 + increment_ms * 0.8` (the side to
  move's clock), clamped to `[0.05s, 20s]`, passed as `time_limit` to
  `find_best_move`
- with a clock-derived budget the depth cap is lifted (`CLOCK_MAX_DEPTH`) so
  iterative deepening's timer (`SearchTimeout`) is what ends the search
- explicit `go depth N` / `go movetime MS` still take precedence
- covered by `tests/test_uci.py`

## Step 6 — Strength & robustness hardening

In rough order of value per effort:

1. **Opening book** (DONE, bridge-side config only): lichess-bot reads a
   polyglot `.bin` book — `engines/komodo.bin` from the donna_opening_books
   repo, enabled under `engine.polyglot` in `config.yml`. Zero engine work.
2. **Search speed**: Python is the ceiling. Cheap wins first: run under
   `pypy3` — DONE: `uv python install pypy3.11` provides the interpreter,
   `uv run --no-project -p pypy3.11 python -m engine.uci` hosts the engine under it, and
   the GUI auto-uses it through `engine/uci_client.py` (measured ~2x on `engine/bench.py`;
   the gain grows with longer time controls as the JIT stays warm). Point
   lichess-bot's engine command at the PyPy invocation above.
   Captures-only quiescence generator: DONE —
   `get_valid_moves(for_ai=True, captures_only=True)` never materializes
   quiet moves at quiescence nodes (the bulk of the tree), roughly halving
   search time to a fixed depth (`bench.py` depth 6: 4.9s → 2.4s CPython).
3. **Persistent transposition table** (DONE): `find_best_move(..., tt=...)`
   accepts a caller-held table; `engine/uci.py` keeps one per game in
   `transposition_table`, clears it on `ucinewgame`, and caps its size.
4. **Eval improvements** (DONE): passed-pawn bonus, king-safety pawn shield,
   and bishop pair added to `evaluate()`.
5. **Endgame draw handling** (DONE): `_insufficient_material` scores dead
   material (K vs K, lone minor, KNN vs K) as an exact draw; lichess-bot's
   `offer_draw` config hooks act on the resulting scores.

## Step 7 — Deployment (macOS / Apple Silicon)

The bot only needs outbound HTTPS, so any always-on box works (a Raspberry Pi,
a $5 VPS, a spare laptop). This project is deployed on a **MacBook Air (M2)**,
so the recipe below is macOS-native: **launchd** (macOS's service manager, the
equivalent of `systemd`) plus **`caffeinate`** to keep the laptop awake.

### The laptop-sleep problem

A laptop's real risk isn't crashes — lichess-bot auto-reconnects on network
drops — it's **sleep**. The screen saver, display sleep, and screen lock are
all harmless: background processes keep running and the connection stays up.
Only *full system sleep* suspends the process and drops the game (an
in-progress game will then flag or abort).

The fix is `caffeinate -s`, which asserts "don't system-sleep" **only while the
wrapped process runs**, and **only on AC power**. So: keep the Mac **plugged
in** (lid may stay open; the screen is free to sleep). Nothing persistent is
changed — unlike `sudo pmset -c disablesleep 1`, there is no global setting to
remember to undo. When the bot stops, the Mac sleeps normally again.

### Run it under launchd

A **LaunchAgent** runs in your logged-in user session, so it inherits your
network access and your uv/PyPy toolchain (a root `LaunchDaemon` would not).
`com.pycheckmate.bot.plist` in this repo is a filled-in-the-blanks template:

1. Copy it to `~/Library/LaunchAgents/com.pycheckmate.bot.plist` and replace the
   `/ABSOLUTE/PATH/TO` and `YOURNAME` placeholders with real paths (the
   lichess-bot clone and your home dir). It wraps `run.sh` in `caffeinate -s`,
   sets `RunAtLoad` (start at login) + `KeepAlive` (restart on crash), points
   `PATH` at Homebrew + `~/.local/bin` (launchd gives a bare PATH, but
   `engines/engine.sh` needs `uv`/PyPy), and logs to `launchd.{out,err}.log`.
2. Validate and start it:

   ```
   plutil -lint ~/Library/LaunchAgents/com.pycheckmate.bot.plist    # -> OK
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pycheckmate.bot.plist
   launchctl list | grep pycheckmate                                 # shows a PID
   tail -f /ABSOLUTE/PATH/TO/lichess-bot/launchd.err.log             # watch it connect
   pmset -g assertions                                               # caffeinate holds PreventSystemSleep
   ```

### Stop / uninstall (back to a normal Mac)

```
launchctl bootout gui/$(id -u)/com.pycheckmate.bot     # stop + disable auto-start
rm ~/Library/LaunchAgents/com.pycheckmate.bot.plist    # remove entirely (optional)
pmset -g assertions                                    # confirm no stray sleep assertion
killall caffeinate 2>/dev/null                         # only if one lingers
```

Because no `pmset`/`sudo` system settings were touched, stopping the job is the
whole cleanup — `caffeinate` dies with the bot and the Mac resumes normal sleep.

### Auto-challenging other bots (matchmaking)

You don't hunt for opponents by hand — lichess-bot's built-in matchmaking does
it. Set `matchmaking.allow_matchmaking: true` in `config.yml` and the bridge
pulls the live online-bot list from Lichess's `/api/bot/online` API, filters by
rating/variant suitability, and (after being idle `challenge_timeout` minutes)
challenges one at random with a time control drawn from `challenge_initial_time`
/ `challenge_increment`. Set `challenge_mode` to `casual`/`rated`, tune
`opponent_rating_difference` for opponent strength, and set
`challenge.accept_bot: true` so bots can challenge back too.

### Operating it: the one rule that keeps it stable

**After any change, restart the agent exactly once and then leave it alone.**

Lichess protects `/api/stream/event` (the connection the bot holds open to
receive challenges and game events) with an **anti-polling rate limit**: open
that stream too many times in a short window and Lichess returns 429s. Every
`launchctl` restart re-opens the stream, so a burst of back-to-back restarts
(e.g. iterating on `config.yml`) trips it. Once tripped, it can spiral: the
bridge retries the reconnect every ~1–2 s while Lichess is asking for a ~60 s
wait, so it re-opens *faster than the cooldown clears* and **cannot recover
while running**. Symptoms: the bot shows online but "accepts challenges without
playing" (the stream is down exactly when a game needs its first move), and
`launchd.err.log` fills with `RateLimitedError: /api/stream/event is
rate-limited`.

To reload config safely, use a single in-place restart and then wait:

```
launchctl kickstart -k gui/$(id -u)/com.pycheckmate.bot   # once
sleep 120 && wc -l ~/PycharmProjects/lichess-bot/launchd.err.log   # 0 == healthy
```

**Recovering from a rate-limit spiral** (err log growing by hundreds of lines):
the fix is *silence*, not more restarts — every restart that hits the limit
renews the penalty.

```
launchctl bootout gui/$(id -u)/com.pycheckmate.bot     # STOP (halts the retry loop)
# wait — minutes for a light trip, up to an hour+ if it was hammered hard today
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pycheckmate.bot.plist   # start once
sleep 120 && wc -l ~/PycharmProjects/lichess-bot/launchd.err.log   # 0 == recovered
```

If the err log is still hundreds of lines two minutes after a start, the
cooldown wasn't long enough: `bootout` again and wait longer before retrying.
lichess-bot self-reconnects fine on *normal* network drops — this trap is
specifically about **how often you restart the process**, so during live
config tuning, change several settings at once and reload just once.

### Learn from the games

Log games (lichess-bot writes PGNs) and skim losses for blunders — feed
concrete positions back into `tests/` as regression FENs via
`GameState.from_fen`.

## Testing pipeline (before going online)

1. `pytest tests/ -q` — engine correctness (perft, zobrist, search sanity).
2. Self-play smoke: `uv run --no-project python -m engine.selfplay` — spawns two
   `engine.uci` processes, plays 20 full games refereed by a `GameState`, and
   exits non-zero on any illegal move or engine crash. Exercises the whole UCI +
   search + make/unmake stack over complete games (the integration coverage
   perft can't give).
3. `lichess-bot` in casual-only mode vs. your own human account.
4. Only then open the challenge gate to the public and rated games.
