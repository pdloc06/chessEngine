# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PyCheckmate is a personal **learning project**: a Pygame chess game with a from-scratch AI engine, plus a UCI adapter that runs it as a Lichess bot (deployed; operations manual in `LICHESS_BOT.md`). Because it's a learning project, existing explanatory comments must be kept, and new comments/docstrings should be written in the same educational style. All docstrings use **NumPy style** (Parameters/Returns sections).

## Commands

The project is uv-managed (`pyproject.toml` + `uv.lock` + `.python-version`); `uv sync` builds `.venv` with CPython 3.14, pygame-ce, pytest, and mypy.

```bash
uv run main.py                      # Run the game (opens the menu, needs a display)
uv run pytest tests/ -q             # Run the full test suite
uv run pytest tests/test_move_finder.py -q                  # One file
uv run pytest tests/test_ai_interface.py::test_perft_initial_position -q   # One test
uv run mypy main.py config.py engine/ gui/ tests/           # Type check (config in mypy.ini)
uv run --no-project python -m engine.uci                    # UCI engine REPL
uv run --no-project python -m engine.bench                  # Engine benchmark (add -p pypy3.11 to compare)
```

Run `pytest` and the full `mypy` command above before finishing any change — they are the project's quality gates.

GUI code can be exercised headlessly with `SDL_VIDEODRIVER=dummy`. PyPy (installed via `uv python install pypy3.11`) hosts the AI subprocess at runtime and can run any `engine/` module; it is auto-detected, never required.

Git pushes must use SSH (`git@github.com:pdloc06/PyCheckmate.git`); the HTTPS remote has no stored credentials. `gh` CLI is available for PRs.

## Architecture

Two packages and a thin root, connected by narrow contracts. `engine/` is pure stdlib (never import pygame there — that's what lets it run under PyPy); `gui/` + `main.py` + `config.py` are the CPython/pygame side.

**`engine/chess_engine.py`** — all rules state. `GameState` holds the board as `list[list[int]]` of small integer piece codes (`0`=empty, `1-6`=white P/N/B/R/Q/K, `7-12`=black), row 0 = rank 8 (Black home). `0 < piece < 7` tests colour and `PIECE_TYPE[piece]` recovers a colour-independent 1-6 type index; `CODE_TO_INT`/`INT_TO_CODE` convert to the legacy `'wP'`/`'--'` strings only at the FEN, SAN/UCI, and GUI-image boundaries. There are **two parallel move pipelines**:

- *UI path*: `get_valid_moves()` returns `Move` objects; the game applies them only via `gs.make_move(move)`, which maintains `move_log`, `state_log`, repetition counts, and does a full Zobrist recompute. Undo via `unmake_move()`.
- *AI path* (hot loop): `get_valid_moves(for_ai=True)` returns 5-tuples `(start_row, start_col, end_row, end_col, move_type)` where type 0=normal, 1=castle, 2=en-passant, 3–6=promotion Q/R/B/N. Search executes them with `make_ai_move()`/`unmake_ai_move()`, which skip the logs, update the Zobrist key incrementally, and return a 4-tuple undo package `(captured_piece, old_enpassant, old_castle_rights, old_zobrist)`. `for_ai=True` also intentionally skips 50-move/threefold hashing — the search layer handles repetition itself via `zobrist_history`.

The two paths meet at `Move.from_ai_tuple(tuple, board)` / `Move.to_ai_tuple()`: an AI result must be converted to a `Move` and applied through `gs.make_move()` so animation/move-log/undo stay in sync — never apply `make_ai_move()` to the real game state. Both `make_ai_move` and `unmake_ai_move` must keep `white_pieces`/`black_pieces` sets exact (move generation iterates those sets, not the board); `tests/test_ai_interface.py` has a random-walk regression test for this.

`GameState.from_fen()`/`to_fen()`, `Move.get_uci_notation()`, `make_null_move()`, and `refresh_derived_state()` (rebuilds all derived caches from a hand-set board — used by test fixtures) round out the interface. Zobrist tables are module-level, deterministically seeded.

**`engine/move_finder.py`** — the search, operating purely on the tuple interface: iterative-deepening negamax with alpha-beta, transposition table keyed by `gs.zobrist_key`, quiescence search, MVV-LVA/killer/history move ordering, null-move pruning, check extension. Entry point: `find_best_move(gs, valid_moves=None, max_depth=4, time_limit=5.0) -> MoveTuple | None`. Evaluation (`evaluate()`) is material + piece-square tables, always from White's perspective. Mate scores use `CHECKMATE_SCORE`/`MATE_THRESHOLD` and are excluded from the TT.

**`engine/pgn.py`** — PGN/SAN import (pure stdlib). SAN tokens are resolved by *matching* against `get_valid_moves()` rather than re-implementing rules; `game_from_pgn()` replays a full PGN into a `GameState` with an intact `move_log`. `looks_like_fen()` is the import screen's FEN-vs-PGN discriminator.

**`engine/analysis.py`** — chess.com-style game review (pure stdlib). Scores are converted to win% (logistic curve) and each move is graded by win% loss: `best/excellent/good/inaccuracy/mistake/blunder` ladder plus `brilliant` (sound sacrifice, via a lightweight attacker-scan heuristic), `great_find` (only good move, verified by a second search excluding the best move), `missed_win`, `book` (small SAN-line opening table, built lazily), and `forced`. Tag string values double as `evaluate_icons/` file stems. `GameAnalysis` runs the whole-game loop on a daemon thread (one search per position; `evals[i]` = position before move i) and supports appending exploration moves mid-analysis; `move_finder.search_position()` is the score-returning search variant it builds on.

**`engine/uci.py` + `engine/uci_client.py`** — the engine-as-a-process pair. `uci.py` is a UCI stdin/stdout adapter (run as `python -m engine.uci` from the repo root); it's the Lichess bot path. `uci_client.py` is the host side: it spawns `engine.uci` under PyPy (found on PATH or via `uv python find pypy`) and speaks UCI to it.

**`main.py` + `gui/`** — Pygame front end. Rules:

- All flip and layout math lives **only** in `gui/graphics.board_to_screen()`/`screen_to_board()` (they account for `config.BOARD_TOP`, the player-bar offset, and `config.BOARD_LEFT`, the eval-bar gutter). Never compute pixel coordinates from row/col elsewhere. `config.BOARD_LEFT` is 0 during normal play; `main.run_review()` sets it to `EVAL_BAR_WIDTH` and widens the window while reviewing, restoring both on exit.
- `gui/review.py` renders the review screen (eval bar, tagged move list, board badges, best-move arrow, FEN/PGN import screen); the event loops driving it (`run_review`, `run_import_menu`) live in main.py like the other loops. Reviews always operate on a dedicated `GameState` copy (`_clone_game_for_review`), never the live game. The in-game "Review Game" button only exists once the game has ended (`get_control_button_rects(show_review=...)`). Playing a non-mainline move mid-review opens a *variation* (its own `GameAnalysis`, shown in place of the mainline list with a "Back to game" strip); the mainline move list is never mutated by variations.
- Piece sets are subdirectories of `pieces/` (discovered by `graphics.list_piece_sets()`, selected via `config.PIECE_SET`); the main menu's selector cycles them and reloads images in place.
- Turn ownership: Player 1 always plays the bottom color — `player_one_color = 'b' if board_flipped else 'w'`. Flipping the board mid-game switches which color the human plays in vs-AI mode; this is intended behavior.
- The AI runs on a daemon thread, never against the live state. Preferred path: the persistent PyPy UCI subprocess (toggled by `config.AI_USE_UCI_ENGINE`; it receives `position startpos moves <all moves>` so repetition history survives). Fallback on any failure: in-process `find_best_move` over an isolated copy (`GameState.from_fen(gs.to_fen())` plus copied `zobrist_history`). Results are tagged with a generation counter; undo/flip/restart bumps the generation so stale results are discarded. The worker writes its result `move` before `generation` so the reader never sees a half-published result.
- Layout/colors/AI limits come from `config.py` (`THEME`, `AI_MAX_DEPTH`, `AI_TIME_LIMIT`, `AI_USE_UCI_ENGINE`, bar heights).

## Measuring engine changes

Engine work is judged by measurement, never by intuition — and the *instrument
depends on what kind of change it is*. Getting this wrong once cost a whole
program of overnight matches (search stages F–J) that returned ~0 net Elo, most
of it unresolvable noise rather than real results.

**Score-neutral changes** (faster evaluation, cheaper move generation, provably
equivalent pruning) — use `uv run --no-project python -m engine.bench`, which
reports node count *and* time:

- **Node count is exactly deterministic, and it is the safety proof.** Identical
  node totals across the bench positions mean the search made every same
  decision, so the change cannot have altered how the engine plays. Guarded by
  `test_node_count_is_reproducible_for_a_seeded_search`.
- This only works because the bench seeds `move_finder._root_rng`. The root
  shuffle changes how much the search prunes, so unseeded counts do not
  reproduce and the whole method silently stops working.
- Time is noisy (~29% run-to-run on this machine). Take best-of-5, run the two
  versions back to back, and only believe a result whose sample ranges are
  disjoint.
- A microbenchmark of just the changed function separates the signal from the
  ~85% of runtime the change doesn't touch.

**Behaviour changes** (pruning that can change a result, evaluation terms, move
ordering, anything touching the clock) — **play real games on Lichess.** This is
the standard workflow now; self-play is a smoke test, not a verdict.

1. Commit the change, then `bot up` — matchmaking on, `challenge_mode: casual`.
2. **Record the version cut immediately**: the exact restart timestamp, in the
   `lichess-game-records-version-cut` memory. PGNs in
   `<lichess-bot clone>/game_records/` carry no version marker, so file mtime is
   the *only* way to tell which engine played a game. Written down after the
   games pile up, it cannot be reconstructed.
3. Leave it running. It stays up until Lucas says `bot down` — do not stop it to
   check on it, and do not restart it to tweak config (see the bot-config
   protocol: batch edits, exactly one down/up cycle).
4. After `bot down`, analyse: `engine.tm_replay` over the new set versus the
   previous one, comparing unspent clock at game end and the per-band error
   rates from `analysis.classify_move`.
5. Repeat for the next change.

Why real games rather than a self-play match: they are played at the actual
deployed time controls, against varied real opponents, and every move is graded
against outcomes the engine actually suffered. A match returns one scalar; a set
of real games says *where* the engine lost and *what it was doing with its
clock* when it did. Real blunders are also the only labels not contaminated by
our own guesses — hand-picked "hard" positions have been measured backwards.

**The self-play clock-fidelity trap** — why the time-management gate was
abandoned twice. `LOW_CLOCK_SECONDS` (25s) and `PANIC_CLOCK_SECONDS` (10s) are
*absolute* thresholds, so a scaled-down test clock moves them to a completely
different point in the game:

| Test clock | Hoarding tier engages |
| --- | --- |
| 90+0 | move 39 — inside the moves-21-40 band the change targets |
| 300+0 (deployed) | move 60 — clear of it |

A 90+0 match therefore measures a version of the change that partially cancels
itself, and it read −45 Elo for exactly that reason. Testing at the real control
costs 24–48h for ±50/±35 Elo. **Any self-play clock that is not the deployed
clock is measuring different code paths than the ones that ship.**

`engine.abtest` still exists for quick sanity checks (`abtest "<baseline
worktree>" <games>`; games are played in colour-reversed pairs from a shared
8-ply book, which is what makes the variance survivable). If it is ever used for
a real verdict again: 400 games minimum, never two matches concurrently, and
`selfplay.DEPTH` must stay above what the movetime can actually reach — it was
6, which let searches end on the ply cap rather than the clock in 4 of 7
realistic positions, so a faster engine had nowhere to spend its speed and
measured as 0 Elo however much it helped.

## Tests

Fixtures in `tests/conftest.py`: `gs` (fresh start position), `custom_gs(board, white_turn=...)` (hand-built board; calls `refresh_derived_state()` for you), `empty_kings_gs`. Engine correctness is anchored by perft node counts — start position depths 1–4 (20 / 400 / 8,902 / 197,281) plus the Kiwipete stress position — in `tests/test_ai_interface.py`; after any change to move generation or make/unmake, those and the random-walk tests are the safety net.
