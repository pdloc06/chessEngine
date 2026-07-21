# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PyCheckmate is a personal **learning project**: a Pygame chess game with a from-scratch AI engine, plus a UCI adapter that runs it as a Lichess bot (deployed; operations manual in `LICHESS_BOT.md`). Because it's a learning project, existing explanatory comments must be kept, and new comments/docstrings should be written in the same educational style. All docstrings use **NumPy style** (Parameters/Returns sections).

## Commands

The project is uv-managed (`pyproject.toml` + `uv.lock` + `.python-version`); `uv sync` builds `.venv` with CPython 3.14, pygame-ce, pytest, and mypy.

```bash
uv run main.py                      # Run the game (opens the menu, needs a display)
uv run pytest tests/ -q             # Run the full test suite
uv run pytest tests/test_search.py -q                        # One file
uv run pytest tests/test_ai_interface.py::test_perft_initial_position -q   # One test
uv run mypy main.py config.py engine/ gui/ tests/           # Type check (config in mypy.ini)
uv run --no-project python -m engine.uci                    # UCI engine REPL
uv run --no-project python -m engine.tools.bench                  # Engine benchmark (add -p pypy3.11 to compare)

# Measurement (see "Measuring engine changes"). All need PYTHONPATH=.
PYTHONPATH=. uv run --no-project python -m engine.tools.calibrate  # Strength vs UCI_Elo-limited Stockfish
PYTHONPATH=. uv run --no-project python -m engine.tools.sprt /tmp/baseline   # SPRT vs a baseline checkout
PYTHONPATH=. uv run --no-project python -m engine.tools.sf_review  # Grade the record directory with Stockfish
PYTHONPATH=. uv run --no-project python -m engine.tools.sf_watch --once      # Drain the analysis backlog
```

External tools these need: `stockfish` (`brew install stockfish`) and
`fastchess` (built from source into `~/.local/bin`; `engine/tools/sprt.py` prints
the recipe if it is missing).

Run `pytest` and the full `mypy` command above before finishing any change — they are the project's quality gates.

GUI code can be exercised headlessly with `SDL_VIDEODRIVER=dummy`. PyPy (installed via `uv python install pypy3.11`) hosts the AI subprocess at runtime and can run any `engine/` module; it is auto-detected, never required.

The Lichess bot is controlled by `bot up` / `bot down` / `bot status` / `bot log`
(`~/.local/bin/bot`). `bot up` also starts the Stockfish analysis watcher, and
`bot down` waits for the current game *and* its analysis to finish before
stopping — `bot down now` skips both waits. Operations manual: `LICHESS_BOT.md`.

Git pushes must use SSH (`git@github.com:pdloc06/PyCheckmate.git`); the HTTPS remote has no stored credentials. `gh` CLI is available for PRs.

## Architecture

Two packages and a thin root, connected by narrow contracts. `engine/` is pure stdlib (never import pygame there — that's what lets it run under PyPy); `gui/` + `main.py` + `config.py` are the CPython/pygame side. Measurement and operations tooling lives in `engine/tools/`, which imports the engine but is never imported by it.

The engine's modules form a strict chain, and keeping it acyclic is a rule rather than an accident:

```
board  <-  movegen  <-  eval  <-  search
```

`board` imports none of the others, so a cycle can only appear by adding an import that points backwards along that chain.

**`engine/board.py`** — board state and attack detection. `GameState` holds the board as `list[list[int]]` of small integer piece codes (`0`=empty, `1-6`=white P/N/B/R/Q/K, `7-12`=black), row 0 = rank 8 (Black home). `0 < piece < 7` tests colour and `PIECE_TYPE[piece]` recovers a colour-independent 1-6 type index; `CODE_TO_INT`/`INT_TO_CODE` convert to the legacy `'wP'`/`'--'` strings only at the FEN, SAN/UCI, and GUI-image boundaries. There are **two parallel move pipelines**:

Attack detection (`check_pins_checks()`, `is_square_attacked()`, `squares_safe_for_castle()`) lives here rather than in `movegen`, because pins and checks are properties of a position and `make_move` needs them to annotate check for SAN. That placement is what keeps the dependency one-way.

**`engine/movegen.py`** — legal move generation, as **free functions taking a `GameState`**, not methods: `generate_legal(gs, for_ai=..., captures_only=...)` and `generate_captures(gs)`. Free functions are what let `board` stay ignorant of `movegen`; a mixin would have forced an import back the other way. There are **two parallel move pipelines**:

- *UI path*: `generate_legal(gs)` returns `Move` objects; the game applies them only via `gs.make_move(move)`, which maintains `move_log`, `state_log`, repetition counts, and does a full Zobrist recompute. Undo via `unmake_move()`.
- *AI path* (hot loop): `generate_legal(gs, for_ai=True)` returns 5-tuples `(start_row, start_col, end_row, end_col, move_type)` where type 0=normal, 1=castle, 2=en-passant, 3–6=promotion Q/R/B/N. Search executes them with `make_ai_move()`/`unmake_ai_move()`, which skip the logs, update the Zobrist key incrementally, and return a 5-tuple undo package `(captured_piece, old_enpassant, old_castle_rights, old_zobrist, old_halfmove_clock)`. `for_ai=True` still skips threefold hashing — the search layer handles repetition itself via `zobrist_history` — but `halfmove_clock` **is** maintained on this path. It was not until 2026-07-21, and the omission meant the search could not see the 50-move rule at all: in a won position with no progress move it scored +300 while the game drifted to a draw, and because equal-scored root moves get shuffled it did so by playing apparently random moves. `search` reads the clock in two places — a hard `DRAW_SCORE` at 100, and `_fade_toward_draw()`, which scales the static evaluation linearly to zero between half-move 40 and 100 so the search gets a gradient it can act on long before the limit. The fade is applied in the search, never inside `evaluate()`, because `_EVAL_CACHE` is keyed on the Zobrist key alone and `halfmove_clock` is not part of that key.

The two paths meet at `Move.from_ai_tuple(tuple, board)` / `Move.to_ai_tuple()`: an AI result must be converted to a `Move` and applied through `gs.make_move()` so animation/move-log/undo stay in sync — never apply `make_ai_move()` to the real game state. Both `make_ai_move` and `unmake_ai_move` must keep `white_pieces`/`black_pieces` sets exact (move generation iterates those sets, not the board); `tests/test_ai_interface.py` has a random-walk regression test for this.

`GameState.from_fen()`/`to_fen()`, `Move.get_uci_notation()`, `make_null_move()`, and `refresh_derived_state()` (rebuilds all derived caches from a hand-set board — used by test fixtures) round out the interface. Zobrist tables are module-level, deterministically seeded.

**`engine/search.py`** — the search, operating purely on the tuple interface: iterative-deepening negamax with alpha-beta, transposition table keyed by `gs.zobrist_key`, quiescence search, MVV-LVA/killer/history move ordering, null-move pruning, check extension. Entry point: `find_best_move(gs, valid_moves=None, max_depth=4, time_limit=5.0) -> MoveTuple | None`. Mate scores use `CHECKMATE_SCORE`/`MATE_THRESHOLD` and are excluded from the TT. `_root_rng` shuffles equal-scored root moves, which is why node counts only reproduce when it is seeded — `engine/tools/bench.py` does exactly that.

**`engine/eval.py`** — static evaluation: material + piece-square tables (always from White's perspective), mobility, pawn structure, rook activity, bishop/knight quality, king shelter, and a mop-up term that converts won pawnless endgames. `evaluate()` must stay a **pure function of the position**, because `_EVAL_CACHE` is keyed on the Zobrist key alone — anything varying independently of the pieces (the 50-move clock is the live example) belongs in `search`, not here.

**`engine/tt.py`** — the transposition-table contract: entry layout `(depth, flag, score, best_move, generation)` and the three flags. Deliberately not a class; the probe runs at every node and a method call there would cost more than it explains.

**`engine/pgn.py`** — PGN/SAN import (pure stdlib). SAN tokens are resolved by *matching* against `generate_legal()` rather than re-implementing rules; `game_from_pgn()` replays a full PGN into a `GameState` with an intact `move_log`. `looks_like_fen()` is the import screen's FEN-vs-PGN discriminator.

**`engine/analysis.py`** — chess.com-style game review (pure stdlib). Scores are converted to win% (logistic curve) and each move is graded by win% loss: `best/excellent/good/inaccuracy/mistake/blunder` ladder plus `brilliant` (sound sacrifice, via a lightweight attacker-scan heuristic), `great_find` (only good move, verified by a second search excluding the best move), `missed_win`, `book` (small SAN-line opening table, built lazily), and `forced`. Tag string values double as `evaluate_icons/` file stems. `GameAnalysis` runs the whole-game loop on a daemon thread (one search per position; `evals[i]` = position before move i) and supports appending exploration moves mid-analysis; `search.search_position()` is the score-returning search variant it builds on.

**`engine/uci.py` + `engine/uci_client.py`** — the engine-as-a-process pair. `uci.py` is a UCI stdin/stdout adapter (run as `python -m engine.uci` from the repo root); it's the Lichess bot path. `uci_client.py` is the host side: it spawns `engine.uci` under PyPy (found on PATH or via `uv python find pypy`) and speaks UCI to it.

**`main.py` + `gui/`** — Pygame front end. Rules:

- All flip and layout math lives **only** in `gui/graphics.board_to_screen()`/`screen_to_board()` (they account for `config.BOARD_TOP`, the player-bar offset, and `config.BOARD_LEFT`, the eval-bar gutter). Never compute pixel coordinates from row/col elsewhere. `config.BOARD_LEFT` is 0 during normal play; `main.run_review()` sets it to `EVAL_BAR_WIDTH` and widens the window while reviewing, restoring both on exit.
- `gui/review.py` renders the review screen (eval bar, tagged move list, board badges, best-move arrow, FEN/PGN import screen); the event loops driving it (`run_review`, `run_import_menu`) live in main.py like the other loops. Reviews always operate on a dedicated `GameState` copy (`_clone_game_for_review`), never the live game. The in-game "Review Game" button only exists once the game has ended (`get_control_button_rects(show_review=...)`). Playing a non-mainline move mid-review opens a *variation* (its own `GameAnalysis`, shown in place of the mainline list with a "Back to game" strip); the mainline move list is never mutated by variations.
- Piece sets are subdirectories of `pieces/` (discovered by `graphics.list_piece_sets()`, selected via `config.PIECE_SET`); the main menu's selector cycles them and reloads images in place.
- All art is **SVG** — pieces (`pieces/<set>/wK.svg`) and review badges (`evaluate_icons/<tag>.svg`) alike. Both loaders rasterize with `pg.image.load_sized_svg()` once per cached size rather than scaling one bitmap, which is what keeps the 16px captured-material icons and move-log badges sharp. Two consequences worth knowing: `load_sized_svg` preserves the source aspect ratio instead of stretching to the requested box (the badges' 18x19 viewBox comes back slightly narrower than tall), and SDL_image's rasterizer ignores `<style>` blocks — an SVG using CSS for its fills loads as a *silently blank* surface. `tests/test_assets.py` guards both by asserting every rasterized asset has opaque pixels; run it after adding any piece set.
- Turn ownership: Player 1 always plays the bottom color — `player_one_color = 'b' if board_flipped else 'w'`. Flipping the board mid-game switches which color the human plays in vs-AI mode; this is intended behavior.
- The AI runs on a daemon thread, never against the live state. Preferred path: the persistent PyPy UCI subprocess (toggled by `config.AI_USE_UCI_ENGINE`; it receives `position startpos moves <all moves>` so repetition history survives). Fallback on any failure: in-process `find_best_move` over an isolated copy (`GameState.from_fen(gs.to_fen())` plus copied `zobrist_history`). Results are tagged with a generation counter; undo/flip/restart bumps the generation so stale results are discarded. The worker writes its result `move` before `generation` so the reader never sees a half-published result.
- Layout/colors/AI limits come from `config.py` (`THEME`, `AI_MAX_DEPTH`, `AI_TIME_LIMIT`, `AI_USE_UCI_ENGINE`, bar heights).

## Measuring engine changes

Engine work is judged by measurement, never by intuition — and the *instrument
depends on what kind of change it is*. Getting this wrong has cost this project
more than any bug: a whole program of overnight matches (search stages F–J) that
returned ~0 net Elo, and then 43 Lichess games that could not answer the
question they were collected for. Both failures were measurement design, not
engine quality.

**Know the baseline first.** The engine measures **~2133 Elo** (2026-07-19,
`engine/tools/calibrate.py`, three Stockfish `UCI_Elo` levels agreeing within 47
points). Nothing gets tuned before a number like this exists, because without
it there is no way to tell a real regression from a hostile sample. See the
`engine-calibrated-strength` memory.

**Score-neutral changes** (faster evaluation, cheaper move generation, provably
equivalent pruning) — `uv run --no-project python -m engine.tools.bench`:

- **Node count is exactly deterministic, and it is the safety proof.** Identical
  node totals across the bench positions mean the search made every same
  decision, so the change cannot have altered how the engine plays. Guarded by
  `test_node_count_is_reproducible_for_a_seeded_search`.
- This only works because the bench seeds `search._root_rng`. The root
  shuffle changes how much the search prunes, so unseeded counts do not
  reproduce and the whole method silently stops working.
- Time is noisy (~29% run-to-run on this machine). Take best-of-5, run the two
  versions back to back, and only believe a result whose sample ranges are
  disjoint.

**Behaviour changes** (pruning that can change a result, evaluation terms, move
ordering, anything touching the clock) — **SPRT**, via
`engine/tools/sprt.py` (a `fastchess` wrapper):

```bash
git worktree add /tmp/baseline master
PYTHONPATH=. uv run --no-project python -m engine.tools.sprt /tmp/baseline
```

- Fixed-size matches are the trap to avoid. 100 games resolve ±70 Elo and 400
  resolve ±35 — so judging a 5–20 Elo change that way asks a question the
  sample cannot answer, which is exactly what stages F–J did. SPRT instead
  stops as soon as the evidence is decisive and declines to answer when the
  change is genuinely neutral.
- **One change in flight at a time.** When five features land together and the
  total is zero, nothing has been learned about any of them — two could be +30
  and three −20. Stage J was reverted with the commit message "never measured
  on its own".
- Concurrency 3 saturates this machine (4 performance cores) and makes it
  unusable; concurrency 1 keeps it responsive. Games are played in
  colour-reversed pairs from `books/uho_5000.epd`, a seeded sample of
  Stockfish's UHO_Lichess_4852_v1 — unbalanced human openings cut the draw
  rate, which raises information per game.

**Absolute strength** — `engine/tools/calibrate.py` plays Stockfish pinned to a
requested Elo via `UCI_LimitStrength`, and brackets the level where we score
50%. Use it after every milestone. It answers "how strong are we", which SPRT
(a *relative* instrument) never can, and it is a foreign opponent, so unlike
self-play it can see mistakes our own evaluation does not understand.

### The clock-fidelity trap

`LOW_CLOCK_FRACTION` and `PANIC_CLOCK_FRACTION` are **fractions of the starting
clock**, and they must stay that way. As absolute seconds (25s and 10s) they sat
at a fixed point on the wall clock but a *moving* point in the game — engaging
around move 60 of a 5+0 game but move 39 of a 90-second test game, inside the
very band a change was targeting. A scaled test clock therefore measured
different code paths than the ones that ship, and read −45 Elo for that reason
alone. Two overnight gates were lost to it.

The remaining absolute quantities are `MOVE_OVERHEAD` + `CLOCK_RESERVE`
(1.15s), which are genuinely physical — network latency does not shrink with
the clock. They eat 0.4% of a 300s clock but 11.5% of a 10s one, so **60s is
the floor** below which the engine is measurably playing a different game.
That is why `engine/tools/sprt.py` defaults to `60+0`.

### Real games

The bot plays **rated** games (`challenge_mode: rated`, opponent bounds
1800–2500 around the calibration). This is not a detail: the first 43 games
were casual, so the rating never left Lichess's provisional 3000, matchmaking
kept pairing us with ~2930 bots, and we lost 42 of 43 — which is very close to
the exact arithmetic of a 795-Elo gap, and says nothing whatever about the
engine. A casual bot cannot self-correct its own matchmaking.

`bot up` starts the bot **and** `engine/tools/sf_watch.py`, which grades each game
with Stockfish in the idle gap before the next one begins and appends a
version-stamped record to `~/.local/share/pycheckmate/game_analysis.jsonl`.
`bot down` waits for the current game *and* the current analysis to finish
before stopping. Records carry per-move centipawn loss, accuracy, and seconds
actually spent (from the PGN's `%clk`), so ACPL, the
inaccuracy/mistake/blunder ladder, per-phase breakdowns and "did we spend time
where we went wrong" are all recoverable without re-searching.

**Never average across `engine_version`.** That is the same error as the casual
games, one level down: a mean over two different engines describes neither.

**Do not commit engine changes while the bot is running.** `sf_watch` reads the
git revision once at startup, but lichess-bot spawns the engine fresh from the
working tree for every game — so an engine commit mid-run means later games play
*new* code while being stamped with the *old* revision, and the records lie
without any warning. Docs-only commits are safe. To ship an engine change:
`bot down`, commit, `bot up` (which records a new version cut).

## Tests

Fixtures in `tests/conftest.py`: `gs` (fresh start position), `custom_gs(board, white_turn=...)` (hand-built board; calls `refresh_derived_state()` for you), `empty_kings_gs`. Engine correctness is anchored by perft node counts — start position depths 1–4 (20 / 400 / 8,902 / 197,281) plus the Kiwipete stress position — in `tests/test_ai_interface.py`; after any change to move generation or make/unmake, those and the random-walk tests are the safety net.
