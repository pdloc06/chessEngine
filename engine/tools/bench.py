"""
Deterministic engine benchmark: perft throughput and fixed-depth searches.

Run it under different interpreters to compare raw engine speed, e.g.:

    uv run --no-project python -m engine.tools.bench                # project CPython
    uv run --no-project -p pypy3.11 python -m engine.tools.bench    # PyPy (JIT-compiled)

Perft exercises move generation + make/unmake only; the search benchmark
exercises the full AI stack (ordering, evaluation, transposition table).
Neither needs pygame, so the script runs on a bare interpreter.

**Why this exists in its current form.** The natural way to judge an engine
change is to play it against the old version and measure Elo — and for
changes that alter *what the engine plays*, that is still the only honest
test. But a self-play match is extraordinarily blunt: 100 games resolve
nothing finer than about +/-70 Elo, and a night of them cannot separate a
genuine 20-Elo gain from noise. Worse, `engine.tools.selfplay`'s ply cap means a
faster engine often has nowhere to spend the speed at all (see the DEPTH
comment there), so speed work can measure as zero however much it helps.

This benchmark is the answer for every change that is *meant to be
score-neutral* — a faster evaluation, cheaper move generation, tighter
pruning that provably cannot change a result. Two numbers matter:

- **nodes** — how many positions the search had to visit. Perfectly
  deterministic: same position, same depth, same root order means the same
  node count unless pruning or ordering genuinely changed. Immune to
  machine load, so a 1% difference is real and readable in a single run.
- **time** — wall clock, which is what actually buys depth online, but is
  noisy and must never be read off a loaded machine.

Together they separate the two ways a change can help: fewer nodes for the
same answer (better pruning) versus the same nodes in less time (faster
code). A change that cuts time while *raising* nodes is a JIT/cache effect
worth a second look.
"""
import sys
import time

from engine.board import GameState
from engine.movegen import generate_legal
from engine.eval import _EVAL_CACHE
from engine import search
from engine.search import _root_rng, find_best_move

# Root move order is shuffled per search (`_root_rng`) so that
# self-play games vary. That shuffle also changes how much the search can
# prune, so node counts are only reproducible once the order is pinned —
# without this seed the whole point of the benchmark quietly evaporates.
RNG_SEED = 20240719

# A spread of positions, not one: pruning and evaluation changes behave very
# differently in a sharp opening (many pieces, deep tactics) than in an
# endgame (few pieces, where the search runs far deeper for the same cost).
# A change that helps one and hurts the other looks like nothing at all when
# only a middlegame is measured.
BENCH_POSITIONS: tuple[tuple[str, str], ...] = (
    ('opening', 'r1bqkb1r/pp2pppp/2np1n2/2p5/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5'),
    ('middlegame', 'r2q1rk1/1p1n1ppp/p1pbpn2/8/2BP4/2N1PN2/PP3PPP/R1BQ1RK1 w - - 0 11'),
    ('tactical', 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1'),
    ('endgame', '8/5pk1/6p1/8/8/1R6/5PPP/6K1 w - - 0 30'),
)

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
    for move in generate_legal(gs, for_ai=True):
        undo = gs.make_ai_move(move)
        total += perft(gs, depth - 1)
        gs.unmake_ai_move(move, undo)
    return total


def bench_search(depth: int = SEARCH_DEPTH) -> tuple[int, float]:
    """
    Search every benchmark position to a fixed depth.

    Each position starts from a cold evaluation cache and its own
    transposition table, so no position's result depends on the ones before
    it and the totals stay comparable between runs.

    Parameters
    ----------
    depth : int, optional
        Fixed search depth for every position. Default is SEARCH_DEPTH.

    Returns
    -------
    tuple of (int, float)
        Total nodes visited and total elapsed seconds across all positions.
    """
    total_nodes = 0
    total_time = 0.0

    print(f'{"position":14} {"nodes":>12} {"time":>9} {"nodes/s":>12}')
    for name, fen in BENCH_POSITIONS:
        # Pin the root shuffle and clear the eval cache so this position's
        # numbers depend only on the engine, not on the run order.
        _root_rng.seed(RNG_SEED)
        _EVAL_CACHE.clear()

        gs = GameState.from_fen(fen)
        start = time.perf_counter()
        # A generous time limit so the target depth always completes: a
        # search cut short by the clock reports whatever it reached, which
        # is not comparable across interpreters or between versions.
        find_best_move(gs, max_depth=depth, time_limit=600.0)
        elapsed = time.perf_counter() - start
        # Module attribute, not a from-import: `search` rebinds this after
        # every search, so a bound copy would stay frozen at its import-time
        # value and this whole instrument would silently report zero.
        nodes = search.last_search_nodes

        total_nodes += nodes
        total_time += elapsed
        print(f'{name:14} {nodes:12,} {elapsed:8.2f}s {nodes / elapsed:12,.0f}')

    print(f'{"TOTAL":14} {total_nodes:12,} {total_time:8.2f}s '
          f'{total_nodes / total_time:12,.0f}')
    return total_nodes, total_time


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
          f'({nodes / elapsed:,.0f} nodes/s)\n')

    # --- Search: the full AI stack at a fixed depth ---
    print(f'search to depth {SEARCH_DEPTH} (seed {RNG_SEED}):')
    bench_search()


if __name__ == '__main__':
    main()
