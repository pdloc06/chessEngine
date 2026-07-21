# PyCheckmate

A chess game written in Python with Pygame, featuring a from-scratch AI engine.
Built as a learning project — the code is heavily commented and documented so it
can be read as a walkthrough of how a chess engine works.

## Features

- Full chess rules: castling, en passant, pawn promotion (with a piece picker),
  check/checkmate/stalemate, threefold repetition, and the 50-move rule.
- Play against another person or against the computer (chosen from the main menu).
- AI engine: iterative-deepening negamax with alpha-beta pruning, aspiration
  windows, a game-long Zobrist-keyed transposition table, quiescence search with
  static-exchange-evaluation (SEE) pruning, MVV-LVA / killer / history move
  ordering, late move reductions, and null-move pruning. The board is stored as
  compact integer piece codes, keeping the hot search loops free of string work.
- Move log panel with algebraic notation and click-to-browse game history.
- **Game review** (chess.com style): after a game ends, a **Review Game**
  button replays it with an evaluation bar, a move list where every move is
  graded (brilliant / great / best / excellent / good / book / inaccuracy /
  mistake / miss / blunder / forced, with icons from `evaluate_icons/`), a
  quality badge on the moved piece, a best-move arrow, and an accuracy score
  for each player.
- **Game analysis** from the main menu: paste or type a FEN position or a
  full PGN game and get the same review experience for any game.
- **Variations** while reviewing: play a move that differs from the game and
  the analysis continues down your side line; a "Back to game" strip returns
  you to the real game at any time.
- Selectable piece sets — drop a new folder of piece images into `pieces/`
  and cycle through the sets from the main menu.
- Board flipping, move animations, and player info bars.
- A minimal UCI adapter (`engine/uci.py`) so the engine can talk to standard chess
  tooling — see `docs/LICHESS_BOT.md` for the Lichess bot manual, and
  `docs/BUILD_LOG.md` for how the engine was built and measured.

## Requirements

- [uv](https://docs.astral.sh/uv/) — the only tool you need to install
  yourself; it manages the Python interpreter (3.14, per `.python-version`)
  and the dependencies (just [pygame-ce](https://pyga.me/) at runtime) from
  the committed lockfile.

## Setup

```bash
# 1. Install uv (see https://docs.astral.sh/uv/getting-started/installation/)
brew install uv                  # macOS; other platforms:
                                 # curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone the repository
git clone https://github.com/pdloc06/PyCheckmate.git
cd PyCheckmate

# 3. Create the environment and install everything from uv.lock
uv sync
```

## Running the game

```bash
uv run main.py
```

Pick your opponent (computer or another person) from the menu, then:

| Action | Control |
| --- | --- |
| Select / move a piece | Left click (click the piece, then the target square) |
| Undo a move | `Ctrl`/`Cmd` + `Z` |
| Reset the game | `Ctrl`/`Cmd` + `R`, or the **Restart Game** button |
| Flip the board | The **Flip** button (in a computer game this also switches which color you play) |
| Browse move history | Click a move in the log, or the `<` / `>` buttons |
| Review the finished game | The **Review Game** button (appears once the game ends) |

Player 1 always plays the color shown at the bottom of the board.

To analyse a game that wasn't played in the program, pick **Game Analysis**
from the main menu and paste (`Ctrl`/`Cmd` + `V`) or type a FEN position or a
PGN game. While reviewing, step through moves with the `<` / `>` buttons or by
clicking the move list — and play any move on the board to explore a
variation.

## Faster AI with PyPy

The engine (the `engine/` package) is pure standard
library, so it can run under [PyPy](https://pypy.org/), whose JIT makes the
search about **2× faster** (more at longer time controls). One command sets
it up:

```bash
uv python install pypy3.11
```

That's it — the game detects PyPy automatically and hosts the AI in a PyPy
subprocess (see `engine/uci_client.py`), while the GUI itself stays on CPython for
pygame. Without PyPy the AI simply searches in-process; set
`AI_USE_UCI_ENGINE = False` in `config.py` to force that. Compare
interpreters yourself with the included benchmark:

```bash
uv run --no-project python -m engine.tools.bench                # CPython
uv run --no-project -p pypy3.11 python -m engine.tools.bench    # PyPy
```

## Running the engine over UCI

The engine speaks a minimal subset of the UCI protocol, enough to plug into
standard chess GUIs and bridges:

```bash
uv run --no-project python -m engine.uci      # or -p pypy3.11 for speed
```

Example session: `position startpos moves e2e4`, then `go depth 4`, and the
engine answers with `bestmove <move>`.

## Development

`uv sync` already installs the dev tools (pytest and mypy). Run the checks:

```bash
uv run pytest tests/ -q          # Test suite (move generation, perft, search, FEN/UCI)
uv run mypy main.py config.py engine/ gui/ tests/
```

For a heavier integration check before deploying the bot, the self-play smoke
test plays two engine processes against each other and fails on any illegal move
or crash:

```bash
uv run --no-project python -m engine.tools.selfplay        # 20 games (pass a number to change)
```

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | Game driver: menu, event loop, turn handling, threaded AI |
| `config.py` | Layout, theme, and AI settings |
| `engine/` | The headless engine package (pure stdlib, PyPy-compatible) |
| `engine/board.py` | Board state and rules: make/unmake, attack detection, FEN, Zobrist hashing |
| `engine/movegen.py` | Legal and capture generation, as free functions over a `GameState` |
| `engine/eval.py` | Static evaluation: material, piece-square tables, positional terms |
| `engine/search.py` | The AI search: negamax, quiescence, ordering, time management |
| `engine/tt.py` | Transposition-table entry layout and flags |
| `engine/tools/` | Measurement and operations tooling (bench, calibrate, sprt, sf_watch) |
| `engine/analysis.py` | Game review: move grading, win-percent model, accuracy |
| `engine/pgn.py` | PGN/SAN and FEN import for the analysis screen |
| `engine/uci.py` | UCI protocol adapter for running the engine outside the GUI |
| `engine/uci_client.py` | Spawns the engine as a (PyPy) subprocess and talks UCI to it |
| `engine/tools/bench.py` | Engine speed benchmark for comparing interpreters |
| `engine/tools/selfplay.py` | Self-play smoke test: two UCI engines play full games, asserts no illegal moves/crashes |
| `gui/` | Rendering: board graphics, animations, menus, panels |
| `gui/review.py` | Review screen rendering: eval bar, graded move list, badges |
| `pieces/` | Piece image assets (one subfolder per selectable piece set) |
| `evaluate_icons/` | Move-quality icons used by the review screen |
| `tests/` | Pytest suite |
| `docs/BUILD_LOG.md` | How the engine was built and measured: benchmarks, dead ends, and what each negative result cost |
| `docs/ENGINE_V2_PLAN.md` | Retrospective and roadmap: what the measurements say, and what to build next |
| `docs/LICHESS_BOT.md` | How the engine runs as a Lichess bot: deployment, operations, traps |
| `engine.sh` | Launcher template pointing lichess-bot at the UCI engine |
| `bot` | control script for the Lichess bot (`bot up/down/status/log`); symlink `~/.local/bin/bot` at this file rather than copying it |
