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
- A minimal UCI adapter (`uci.py`) so the engine can talk to standard chess
  tooling — see `LICHESS_BOT_PLAN.md` for the Lichess bot roadmap.

## Requirements

- Python 3.10 or newer (developed on 3.14)
- The packages in `requirements.txt` (currently just [pygame-ce](https://pyga.me/))

## Setup

```bash
# 1. Clone the repository
git clone https://github.com/pdloc06/PyCheckmate.git
cd PyCheckmate

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install the dependencies
pip install -r requirements.txt
```

## Running the game

```bash
python main.py
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

## Running the engine over UCI

The engine speaks a minimal subset of the UCI protocol, enough to plug into
standard chess GUIs and bridges:

```bash
python uci.py
```

Example session: `position startpos moves e2e4`, then `go depth 4`, and the
engine answers with `bestmove <move>`.

## Development

Install the dev tools (pytest and mypy) on top of the runtime dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the checks:

```bash
pytest tests/ -q                 # Test suite (move generation, perft, search, FEN/UCI)
python -m mypy chess_engine.py move_finder.py config.py main.py uci.py gui/ tests/
```

## Project layout

| Path | Purpose |
| --- | --- |
| `chess_engine.py` | Game state and rules: board, move generation, make/unmake, FEN, Zobrist hashing |
| `move_finder.py` | The AI search and evaluation |
| `main.py` | Game driver: menu, event loop, turn handling, threaded AI |
| `gui/` | Rendering: board graphics, animations, menus, panels |
| `uci.py` | UCI protocol adapter for running the engine outside the GUI |
| `config.py` | Layout, theme, and AI settings |
| `pieces/` | Piece image assets |
| `tests/` | Pytest suite |
