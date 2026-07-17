# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PyCheckmate is a personal **learning project**: a Pygame chess game with a from-scratch AI engine, plus a UCI adapter aimed at running as a Lichess bot (roadmap in `LICHESS_BOT_PLAN.md`). Because it's a learning project, existing explanatory comments must be kept, and new comments/docstrings should be written in the same educational style. All docstrings use **NumPy style** (Parameters/Returns sections).

## Commands

The venv lives at `.venv/` (Python 3.14, pygame-ce, pytest, mypy). Activate it or prefix commands with `.venv/bin/`.

```bash
python main.py                      # Run the game (opens the menu, needs a display)
pytest tests/ -q                    # Run the full test suite
pytest tests/test_move_finder.py -q                     # One file
pytest tests/test_ai_interface.py::test_perft_initial_position -q   # One test
python -m mypy chess_engine.py move_finder.py config.py main.py uci.py gui/ tests/   # Type check (config in mypy.ini)
python uci.py                       # UCI engine REPL (e.g. echo "position startpos\ngo depth 3" | python uci.py)
```

Run `pytest` and the full `mypy` command above before finishing any change — they are the project's quality gates.

GUI code can be exercised headlessly with `SDL_VIDEODRIVER=dummy python ...`.

Git pushes must use SSH (`git@github.com:pdloc06/PyCheckmate.git`); the HTTPS remote has no stored credentials.

## Architecture

Three layers, connected by narrow contracts:

**`chess_engine.py`** — all rules state. `GameState` holds the board as `list[list[str]]` of `'wP'…'bK'`/`'--'` codes, row 0 = rank 8 (Black home). There are **two parallel move pipelines**:

- *UI path*: `get_valid_moves()` returns `Move` objects; the game applies them only via `gs.make_move(move)`, which maintains `move_log`, `state_log`, repetition counts, and does a full Zobrist recompute. Undo via `undo_move()`.
- *AI path* (hot loop): `get_valid_moves(for_ai=True)` returns 5-tuples `(start_row, start_col, end_row, end_col, move_type)` where type 0=normal, 1=castle, 2=en-passant, 3–6=promotion Q/R/B/N. Search executes them with `make_ai_move()`/`unmake_ai_move()`, which skip the logs, update the Zobrist key incrementally, and return a 4-tuple undo package `(captured_piece, old_enpassant, old_castle_rights, old_zobrist)`. `for_ai=True` also intentionally skips 50-move/threefold hashing — the search layer handles repetition itself via `zobrist_history`.

The two paths meet at `Move.from_ai_tuple(tuple, board)` / `Move.to_ai_tuple()`: an AI result must be converted to a `Move` and applied through `gs.make_move()` so animation/move-log/undo stay in sync — never apply `make_ai_move()` to the real game state. Both `make_ai_move` and `unmake_ai_move` must keep `white_pieces`/`black_pieces` sets exact (move generation iterates those sets, not the board); `tests/test_ai_interface.py` has a random-walk regression test for this.

`GameState.from_fen()`/`to_fen()`, `Move.get_uci_notation()`, `make_null_move()`, and `refresh_derived_state()` (rebuilds all derived caches from a hand-set board — used by test fixtures) round out the interface. Zobrist tables are module-level, deterministically seeded.

**`move_finder.py`** — the search, operating purely on the tuple interface: iterative-deepening negamax with alpha-beta, transposition table keyed by `gs.zobrist_key`, quiescence search, MVV-LVA/killer/history move ordering, null-move pruning, check extension. Entry point: `find_best_move(gs, valid_moves=None, max_depth=4, time_limit=5.0) -> MoveTuple | None`. Evaluation (`evaluate()`) is material + piece-square tables, always from White's perspective. Mate scores use `CHECKMATE_SCORE`/`MATE_THRESHOLD` and are excluded from the TT.

**`main.py` + `gui/`** — Pygame front end. Rules:

- All flip and layout math lives **only** in `gui/graphics.board_to_screen()`/`screen_to_board()` (they account for `config.BOARD_TOP`, the player-bar offset). Never compute pixel coordinates from row/col elsewhere.
- Turn ownership: Player 1 always plays the bottom color — `player_one_color = 'b' if board_flipped else 'w'`. Flipping the board mid-game switches which color the human plays in vs-AI mode; this is intended behavior.
- The AI runs on a daemon thread over an isolated copy (`GameState.from_fen(gs.to_fen())` plus copied `zobrist_history`), never the live state. Results are tagged with a generation counter; undo/flip/restart bumps the generation so stale results are discarded. The worker writes its result `move` before `generation` so the reader never sees a half-published result.
- Layout/colors/AI limits come from `config.py` (`THEME`, `AI_MAX_DEPTH`, `AI_TIME_LIMIT`, bar heights).

**`uci.py`** — thin UCI stdin/stdout adapter over the same engine, for the Lichess bot path. Comments prefixed `AI_PLANNING` mark roadmap extension points (time controls, pondering) tied to `LICHESS_BOT_PLAN.md` — keep them.

## Tests

Fixtures in `tests/conftest.py`: `gs` (fresh start position), `custom_gs(board, white_turn=...)` (hand-built board; calls `refresh_derived_state()` for you), `empty_kings_gs`. Engine correctness is anchored by perft node counts (20/400/8902 from the start position) — after any change to move generation or make/unmake, the perft and random-walk tests in `tests/test_ai_interface.py` are the safety net.
