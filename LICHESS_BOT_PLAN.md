# Plan: Running PyCheckmate as a Lichess Bot

This is the roadmap for taking the engine in this repository online as a
`BOT`-flagged account on lichess.org. Steps 1–2 are already implemented in
code; the rest is operational work. Code locations that matter are marked
with `AI_PLANNING` comments in the source.

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
Lichess servers  <—HTTPS/JSON—>  lichess-bot (Python bridge)  <—UCI text—>  uci.py  —>  move_finder.py + chess_engine.py
```

## Step 1 — Engine prerequisites (DONE in this repo)

The engine must be able to reconstruct any position the server describes and
speak UCI move notation:

- `GameState.from_fen()` / `to_fen()` — positions arrive as FEN (`chess_engine.py`)
- `Move.get_uci_notation()` — moves leave as UCI strings (`chess_engine.py`)
- `Move.from_ai_tuple()` — converts search output to full moves (`chess_engine.py`)
- Zobrist keys + repetition awareness in search (`move_finder.py`) so the bot
  doesn't blunder into (or miss) threefold repetition draws online

## Step 2 — UCI adapter (DONE: `uci.py`)

`uci.py` implements the minimal command set lichess-bot needs:
`uci`, `isready`, `ucinewgame`, `position startpos|fen ... moves ...`,
`go depth/movetime`, `quit`.

Verify locally:

```
$ python uci.py
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
   `#!/bin/sh` + `exec /path/to/.venv/bin/python /path/to/repo/uci.py`
   (lichess-bot expects an executable, not a Python module).
4. Run `python lichess-bot.py -v`, then challenge your bot from your own
   account and play a game.

## Step 5 — Time management (NEXT code task)

Currently `uci.py` honors `go depth N` and `go movetime MS` but ignores the
clock fields (`wtime`, `btime`, `winc`, `binc`) that Lichess sends. Implement
in `handle_go` (marked with `AI_PLANNING`):

- budget per move ≈ `remaining_ms / 30 + increment_ms * 0.8`
- clamp to `[0.05s, 20s]`; pass as `time_limit` to `find_best_move`
- keep iterative deepening as the interrupt mechanism (already supported via
  `SearchTimeout`)

## Step 6 — Strength & robustness hardening

In rough order of value per effort:

1. **Opening book**: lichess-bot can use a polyglot `.bin` book on the bridge
   side (zero engine work) — enable it to avoid burning clock in the opening.
2. **Search speed**: Python is the ceiling. Cheap wins first: run under
   `pypy3` (often 5-20x on this kind of code), then profile
   (`python -m cProfile -s tottime`) — `get_valid_moves` dominates; a
   captures-only generator for quiescence is the single best structural win.
3. **Persistent transposition table** across moves of the same game (today
   `SearchInfo` is rebuilt per move; keep the dict on a module-level object
   keyed by game id, clear it on `ucinewgame`).
4. **Eval improvements**: passed-pawn bonus, king-safety pawn shield, bishop
   pair — each is a few lines against the existing `evaluate()` loop.
5. **Endgame draw handling**: insufficient-material detection so the bot
   offers/accepts draws sensibly (lichess-bot has config hooks for this).

## Step 7 — Deployment

- Any always-on box works: a Raspberry Pi, a $5 VPS, or a spare laptop; the
  bot only needs outbound HTTPS.
- Run under `systemd` or `tmux`; lichess-bot auto-reconnects on network
  drops.
- Log games (lichess-bot writes PGNs) and skim losses for blunders — feed
  concrete positions back into `tests/` as regression FENs via
  `GameState.from_fen`.

## Testing pipeline (before going online)

1. `pytest tests/ -q` — engine correctness (perft, zobrist, search sanity).
2. Self-play smoke: two `uci.py` processes under `cutechess-cli` or a tiny
   driver script, 20+ games, assert no crashes/illegal moves.
3. `lichess-bot` in casual-only mode vs. your own human account.
4. Only then open the challenge gate to the public and rated games.
