"""
The headless chess engine package: rules, search, and UCI plumbing.

Nothing in here imports pygame, so the whole package runs under any bare
Python interpreter — including PyPy, where the JIT makes the search about
twice as fast (see uci_client.py). The GUI lives in the sibling `gui`
package and talks to this one through GameState/Move or, when PyPy is
available, over the UCI protocol via a subprocess.

Modules
-------
chess_engine : board state, move generation, make/unmake, FEN, Zobrist keys
move_finder  : negamax search with alpha-beta, TT, quiescence, and friends
uci          : UCI protocol adapter (run with `python -m engine.uci`)
uci_client   : host-side client that spawns `engine.uci` as a subprocess
bench        : speed benchmark for comparing interpreters
"""
