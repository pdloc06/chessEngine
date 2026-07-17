# PyCheckmate

A chess game written in Python with Pygame, featuring a from-scratch AI engine.
Built as a learning project — the code is heavily commented and documented so it
can be read as a walkthrough of how a chess engine works.

## Features

- Full chess rules: castling, en passant, pawn promotion (with a piece picker),
  check/checkmate/stalemate, threefold repetition, and the 50-move rule.
- Play against another person or against the computer (chosen from the main menu).
- AI engine: iterative-deepening negamax with alpha-beta pruning, a
  Zobrist-keyed transposition table, quiescence search, MVV-LVA / killer /
  history move ordering, and null-move pruning.
- Move log panel with algebraic notation and click-to-browse game history.
- Board flipping, move animations, and player info bars.
- A minimal UCI adapter (`engine/uci.py`) so the engine can talk to standard chess
  tooling — see `LICHESS_BOT_PLAN.md` for the Lichess bot roadmap.

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

Player 1 always plays the color shown at the bottom of the board.

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
uv run --no-project python -m engine.bench                # CPython
uv run --no-project -p pypy3.11 python -m engine.bench    # PyPy
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

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | Game driver: menu, event loop, turn handling, threaded AI |
| `config.py` | Layout, theme, and AI settings |
| `engine/` | The headless engine package (pure stdlib, PyPy-compatible) |
| `engine/chess_engine.py` | Game state and rules: board, move generation, make/unmake, FEN, Zobrist hashing |
| `engine/move_finder.py` | The AI search and evaluation |
| `engine/uci.py` | UCI protocol adapter for running the engine outside the GUI |
| `engine/uci_client.py` | Spawns the engine as a (PyPy) subprocess and talks UCI to it |
| `engine/bench.py` | Engine speed benchmark for comparing interpreters |
| `gui/` | Rendering: board graphics, animations, menus, panels |
| `pieces/` | Piece image assets |
| `tests/` | Pytest suite |
