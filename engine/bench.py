"""
Quick engine benchmark: perft node throughput and a fixed-depth search.

Run it under different interpreters to compare raw engine speed, e.g.:

    uv run --no-project python -m engine.bench                # project CPython
    uv run --no-project -p pypy3.11 python -m engine.bench    # PyPy (JIT-compiled)

Perft exercises move generation + make/unmake only; the search timing
exercises the full AI stack (ordering, evaluation, transposition table).
Neither needs pygame, so the script runs on a bare interpreter.
"""
import sys
import time

from engine import move_finder
from engine.chess_engine import GameState

# A quiet middlegame position gives the search more to chew on than the
# symmetric starting position (more captures, both sides developed)
BENCH_FEN = 'r1bqkb1r/pp2pppp/2np1n2/2p5/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5'

# Workloads are sized to run for several seconds: PyPy's JIT needs roughly
# a second of hot execution before compiled code takes over, so very short
# runs understate its speed (real searches run warm, like these do)
SEARCH_DEPTH = 6
PERFT_DEPTH = 5  # 4,865,609 nodes from the starting position


def perft(gs: GameState, depth: int) -> int:
    """Count leaf nodes of the legal move tree via the AI make/unmake path."""
    if depth == 0:
        return 1
    total = 0
    for move in gs.get_valid_moves(for_ai=True):
        undo = gs.make_ai_move(move)
        total += perft(gs, depth - 1)
        gs.unmake_ai_move(move, undo)
    return total


def main() -> None:
    """Run the perft and search benchmarks and print a small report."""
    impl = sys.implementation.name
    version = '.'.join(str(part) for part in sys.version_info[:3])
    print(f'Interpreter: {impl} {version}')

    # --- Perft: raw move generation + make/unmake throughput ---
    gs = GameState()
    start = time.perf_counter()
    nodes = perft(gs, PERFT_DEPTH)
    elapsed = time.perf_counter() - start
    print(f'perft({PERFT_DEPTH}): {nodes:,} nodes in {elapsed:.2f}s '
          f'({nodes / elapsed:,.0f} nodes/s)')

    # --- Search: the full AI stack at a fixed depth ---
    # Generous time limit so the run always completes the target depth and
    # the comparison across interpreters stays apples-to-apples
    gs = GameState.from_fen(BENCH_FEN)
    start = time.perf_counter()
    best = move_finder.find_best_move(
        gs, max_depth=SEARCH_DEPTH, time_limit=600.0
    )
    elapsed = time.perf_counter() - start
    print(f'search(depth={SEARCH_DEPTH}): best={best} in {elapsed:.2f}s')


if __name__ == '__main__':
    main()
