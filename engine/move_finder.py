"""
AI move finder built on a negamax alpha-beta search framework.

Implements the classic "strong engine" stack on top of the lightweight
tuple-move interface exposed by `chess_engine.GameState`:

- Iterative deepening with a soft time limit
- Negamax with alpha-beta pruning
- Transposition table keyed by incremental Zobrist hashes
- Quiescence search (captures/promotions only) to avoid horizon effects
- Move ordering: TT move > MVV-LVA captures > promotions > killers > history
- Null-move pruning and check extensions
- Late move reductions, aspiration windows, SEE-pruned quiescence
- Reverse futility and frontier futility pruning near the leaves
- Static evaluation: material, piece-square tables, pawn structure, rook
  activity, and king safety, tapered between middlegame and endgame

The search mutates the GameState in place via `make_ai_move()` /
`unmake_ai_move()` and always restores it before returning, so the caller's
state is untouched. When running the search on a background thread, pass a
`copy.deepcopy` of the GameState so the UI can keep rendering the original.
"""
import math
import random
import time
from collections.abc import Callable

from engine.chess_engine import (
    AI_PROMO_TYPE, GameState, PIECE_TYPE,
    EMPTY, PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING,
    WP, WN, WB, WR, WQ, WK, BP, BN, BB, BR, BQ, BK,
    ALL_DIRECTIONS, DIAGONAL_DIRECTIONS, KNIGHT_DELTAS, ORTHOGONAL_DIRECTIONS,
)

# Type alias for the lightweight move format shared with chess_engine
MoveTuple = tuple[int, int, int, int, int]

# Type alias for the transposition table: zobrist_key -> (depth, flag, score,
# best_move, generation). Callers may hold one of these across searches (the
# UCI adapter keeps a game-long table) and pass it to `find_best_move`. The
# `generation` field stamps which search wrote the entry so the replacement
# policy can evict entries left over from earlier moves (see `_negamax`).
TTable = dict[int, tuple[int, int, int, MoveTuple | None, int]]

# Evaluation cache: zobrist_key -> static score. The search revisits the same
# position many times (transpositions, aspiration re-searches, quiescence
# stand-pats), and the static evaluation is a pure function of the position —
# so each position only ever needs the full 64-square scan once. Unlike the
# transposition table there is nothing to invalidate: a position's static
# score never changes, so the cache safely persists across searches and is
# simply rebuilt when it grows past its bound (entries are ints, so the bound
# keeps it well under the TT's footprint).
_EVAL_CACHE: dict[int, int] = {}
_EVAL_CACHE_MAX = 1_000_000

# --- Evaluation constants ---
# Indexed by the integer piece code (1-12); both colours share a value. Index 0
# (empty) and the kings (6, 12) are 0 so material scans can add unconditionally.
PIECE_VALUES: tuple[int, ...] = (
    0, 100, 320, 330, 500, 900, 0,   # empty, wP wN wB wR wQ wK
       100, 320, 330, 500, 900, 0,   # bP bN bB bR bQ bK
)

CHECKMATE_SCORE = 100_000
MATE_THRESHOLD = 90_000  # Scores beyond this are "mate in N" scores
DRAW_SCORE = 0

# --- Positional evaluation terms ---
# Two bishops cover both square colors; the pair is worth a few tenths of a
# pawn beyond the pieces' individual values.
BISHOP_PAIR_BONUS = 30

# Rook activity: rooks earn their keep on files the pawns have
# vacated. A semi-open file (own pawns gone) gives the rook targets; a fully
# open file gives it the whole board; the 7th rank attacks the enemy's pawn
# line and boxes in the king. These are the terms whose absence let the bot
# shuffle its rooks along the back rank while its first online loss was
# being squeezed (lichess.org/GDTprQUM).
ROOK_SEMI_OPEN_FILE_BONUS = 10
ROOK_OPEN_FILE_BONUS = 20
ROOK_ON_SEVENTH_BONUS = 20

# Mobility (loss-review follow-up): a small bonus per square a piece can move
# to or capture on. The 12-game loss review showed the bot losing by grind —
# slightly passive pieces on every move rather than tactical mistakes — and
# mobility is the classic cure: cramped positions score worse, so the search
# starts preferring moves that give its pieces room and deny the opponent's.
# Indexed by piece type. Knights gain most per square (eight is their whole
# world), queens least (their raw counts are huge and mostly noise); pawns
# score zero (their "mobility" is structure, handled above) and so do kings
# (freedom to wander is not a middlegame asset).
MOBILITY_BONUS = (0, 0, 4, 3, 2, 1, 0)
MOBILITY_DIRECTIONS: dict[int, tuple[tuple[int, int], ...]] = {
    BISHOP: DIAGONAL_DIRECTIONS,
    ROOK: ORTHOGONAL_DIRECTIONS,
    QUEEN: ALL_DIRECTIONS,
}


def _rays_from(row: int, col: int, directions: tuple[tuple[int, int], ...]
               ) -> tuple[tuple[tuple[int, int], ...], ...]:
    """
    List the on-board squares along each ray leaving (row, col).

    Returns one tuple of squares per direction, in order walking outward.
    Rays that leave the board immediately are dropped, so a caller iterating
    the result never handles an empty ray.
    """
    rays = []
    for dr, dc in directions:
        ray = []
        r, c = row + dr, col + dc
        while 0 <= r < 8 and 0 <= c < 8:
            ray.append((r, c))
            r += dr
            c += dc
        if ray:
            rays.append(tuple(ray))
    return tuple(rays)


# Precomputed ray and knight-hop targets for the mobility scan. Mobility is
# the single most expensive term in `evaluate` — a profile of a depth-6 search
# put `_mobility` at 14% of *total* search time, called once per non-pawn
# piece per evaluation — and almost all of that cost was the bounds test
# `0 <= r <= 7 and 0 <= c <= 7` re-evaluated at every step of every ray. The
# board's geometry never changes, so those squares can be enumerated once at
# import and simply walked afterwards. Purely an optimization: the squares
# visited, and therefore every score, are identical.
_MOBILITY_RAYS: dict[int, tuple[tuple[tuple[tuple[tuple[int, int], ...], ...], ...], ...]] = {
    piece_type: tuple(
        tuple(_rays_from(row, col, directions) for col in range(8))
        for row in range(8)
    )
    for piece_type, directions in MOBILITY_DIRECTIONS.items()
}
_KNIGHT_TARGETS: tuple[tuple[tuple[tuple[int, int], ...], ...], ...] = tuple(
    tuple(
        tuple((row + dr, col + dc) for dr, dc in KNIGHT_DELTAS
              if 0 <= row + dr < 8 and 0 <= col + dc < 8)
        for col in range(8)
    )
    for row in range(8)
)

# Pawn-structure penalties. Doubled pawns blockade each other and
# can't create passers; isolated pawns have no pawn that can ever defend
# them, so they tie a piece to the job. Each is charged per offending pawn
# beyond the first / per isolated pawn.
DOUBLED_PAWN_PENALTY = 15
ISOLATED_PAWN_PENALTY = 12
# Passed-pawn bonus indexed by the pawn's row from White's perspective
# (row 1 = one step from promotion; rows 0 and 7 can't hold a pawn).
# Black pawns index with the mirrored row, matching the PST convention.
PASSED_PAWN_BONUS = (0, 120, 80, 50, 30, 20, 10, 0)
# Per-pawn bonus for pawns sheltering the king (middlegame only)
KING_SHIELD_BONUS = 12

# Piece-square tables (white's perspective, row 0 = rank 8).
# Values follow Tomasz Michniewski's "Simplified Evaluation Function".
# Black uses the same tables mirrored vertically (row -> 7 - row).
PST: dict[int, tuple[tuple[int, ...], ...]] = {
    PAWN: (
        (0, 0, 0, 0, 0, 0, 0, 0),
        (50, 50, 50, 50, 50, 50, 50, 50),
        (10, 10, 20, 30, 30, 20, 10, 10),
        (5, 5, 10, 25, 25, 10, 5, 5),
        (0, 0, 0, 20, 20, 0, 0, 0),
        (5, -5, -10, 0, 0, -10, -5, 5),
        (5, 10, 10, -20, -20, 10, 10, 5),
        (0, 0, 0, 0, 0, 0, 0, 0),
    ),
    KNIGHT: (
        (-50, -40, -30, -30, -30, -30, -40, -50),
        (-40, -20, 0, 0, 0, 0, -20, -40),
        (-30, 0, 10, 15, 15, 10, 0, -30),
        (-30, 5, 15, 20, 20, 15, 5, -30),
        (-30, 0, 15, 20, 20, 15, 0, -30),
        (-30, 5, 10, 15, 15, 10, 5, -30),
        (-40, -20, 0, 5, 5, 0, -20, -40),
        (-50, -40, -30, -30, -30, -30, -40, -50),
    ),
    BISHOP: (
        (-20, -10, -10, -10, -10, -10, -10, -20),
        (-10, 0, 0, 0, 0, 0, 0, -10),
        (-10, 0, 5, 10, 10, 5, 0, -10),
        (-10, 5, 5, 10, 10, 5, 5, -10),
        (-10, 0, 10, 10, 10, 10, 0, -10),
        (-10, 10, 10, 10, 10, 10, 10, -10),
        (-10, 5, 0, 0, 0, 0, 5, -10),
        (-20, -10, -10, -10, -10, -10, -10, -20),
    ),
    ROOK: (
        (0, 0, 0, 0, 0, 0, 0, 0),
        (5, 10, 10, 10, 10, 10, 10, 5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (0, 0, 0, 5, 5, 0, 0, 0),
    ),
    QUEEN: (
        (-20, -10, -10, -5, -5, -10, -10, -20),
        (-10, 0, 0, 0, 0, 0, 0, -10),
        (-10, 0, 5, 5, 5, 5, 0, -10),
        (-5, 0, 5, 5, 5, 5, 0, -5),
        (0, 0, 5, 5, 5, 5, 0, -5),
        (-10, 5, 5, 5, 5, 5, 0, -10),
        (-10, 0, 5, 0, 0, 0, 0, -10),
        (-20, -10, -10, -5, -5, -10, -10, -20),
    ),
}

KING_MID_PST: tuple[tuple[int, ...], ...] = (
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-20, -30, -30, -40, -40, -30, -30, -20),
    (-10, -20, -20, -20, -20, -20, -20, -10),
    (20, 20, 0, 0, 0, 0, 20, 20),
    (20, 30, 10, 0, 0, 10, 30, 20),
)

KING_END_PST: tuple[tuple[int, ...], ...] = (
    (-50, -40, -30, -20, -20, -30, -40, -50),
    (-30, -20, -10, 0, 0, -10, -20, -30),
    (-30, -10, 20, 30, 30, 20, -10, -30),
    (-30, -10, 30, 40, 40, 30, -10, -30),
    (-30, -10, 30, 40, 40, 30, -10, -30),
    (-30, -10, 20, 30, 30, 20, -10, -30),
    (-30, -30, 0, 0, 0, 0, -30, -30),
    (-50, -30, -30, -30, -30, -30, -30, -50),
)

# Tapered evaluation: instead of one hard "now it's an endgame"
# switch, phase-dependent terms blend smoothly between their middlegame and
# endgame values as pieces come off. The phase is the fraction of non-pawn
# material still on the board, scaled to 0-256 (256 = everything still on;
# integer arithmetic so the hot loop never touches floats). A hard threshold
# makes the eval jump discontinuously when one exchange crosses it — the
# search then sees phantom score swings for trades that change nothing.
PHASE_MAX = 256
# Both sides' full non-pawn complement: 2 each of N/B/R plus a queen, twice.
PHASE_MATERIAL_MAX = 2 * (2 * 320 + 2 * 330 + 2 * 500 + 900)

# Passed pawns promote in endgames; with heavy pieces still on, the same
# passer is often just a target. PASSED_PAWN_BONUS (above) serves as the
# middlegame column; this endgame column raises the stakes by half again.
PASSED_PAWN_BONUS_END = (0, 180, 120, 75, 45, 30, 15, 0)

# Transposition table bound flags
TT_EXACT, TT_LOWER, TT_UPPER = 0, 1, 2

# Null-move pruning depth reduction
NULL_MOVE_REDUCTION = 2

# Futility pruning margins. Both prunes trust the static eval near
# the leaves, where a search this shallow couldn't recover a big material
# swing anyway:
# - Reverse futility: if the *side to move* is already ahead of beta by a
#   comfortable margin, the opponent won't allow this line — fail high now
#   instead of searching it.
# - Frontier futility: if the side to move is far *below* alpha, a quiet
#   move can't close the gap within the remaining depth — skip it and let
#   captures/checks speak for themselves.
# Margins grow with remaining depth because deeper subtrees can swing more.
RFP_MAX_DEPTH = 3
RFP_MARGIN = 120                  # per remaining ply
FUTILITY_MAX_DEPTH = 2
FUTILITY_MARGIN = (0, 150, 300)   # indexed by remaining depth

# Delta pruning margin for quiescence (search-review stage G): a capture is
# skipped when even winning the victim outright, plus this safety cushion for
# positional swing, cannot lift the line back to alpha.
DELTA_MARGIN = 200

HISTORY_EXEMPT_SCORE = 64         # history score that buys a shallower reduction

# Late move reductions: quiet moves ordered past the first few are scouted
# shallower, and only re-searched at full depth if the scout unexpectedly
# beats alpha. The reduction is *log-scaled* (search-review stage I): the
# deeper the node and the later the move, the less the ordering believes in
# it, so the shallower its scout — ln(depth)·ln(index)/2.25 plies, the shape
# every strong engine converged on. A move with a proven history record is
# reduced one ply less: its past cutoffs have earned it a closer look. The
# table is precomputed once; index 0 rows/columns stay 0 (never used — the
# first moves are never reduced).
LMR_MIN_DEPTH = 3
LMR_MIN_MOVE_INDEX = 3
LMR_TABLE: tuple[tuple[int, ...], ...] = tuple(
    tuple(
        int(0.75 + math.log(d) * math.log(m) / 2.25) if d and m else 0
        for m in range(64)
    )
    for d in range(64)
)

# Dynamic time management: how much of the budget may already be spent and
# still allow another deepening iteration to start.
#
# This used to be 0.45, on the reasoning that an iteration costs ~3-5x the one
# before it, so one started late would be aborted mid-search — "time spent,
# nothing gained". The second half of that was true only because `_search_root`
# then *discarded* an aborted iteration wholesale. It no longer does: root
# moves are ordered best-first, so a partial pass either confirms the previous
# best or replaces it with a move that outscored it one ply deeper, and both
# beat stopping early.
#
# Measured, the old value left a third of the clock unused — 58% of a 3s budget
# and 69% of a 5s one actually spent. Since an aborted iteration now returns
# real information, the only reason left to hold back is to avoid starting one
# with so little left that it cannot finish even a single root move, so the
# gate sits just short of the full budget.
SOFT_STOP_FRACTION = 0.9

# Panic extension: when a completed iteration's score falls this many
# centipawns below the previous iteration's, the engine has just "seen"
# something bad — the moment a fixed budget is most likely to lock in a
# blunder. The soft gate then widens to the full soft budget and the hard
# abort moves out to the caller's hard limit (when one is given), letting
# the search finish discovering what it started to see.
#
# This crude binary rule is better than it looks, and two attempts to improve
# on it were measured and dropped. Scored by `engine.tm_replay` against the
# bot's real games — how much longer it thinks on positions it actually
# blundered versus positions it played well — this rule gets 1.34x. Making
# the response continuous *and* adding a root best-move stability signal got
# 1.11x; keeping only the continuous part plus an early-exit discount for
# settled searches got 1.36x, indistinguishable from doing nothing.
#
# The reason the stability signal backfires is worth keeping: in quiet
# endgames the root best move flaps between moves of *identical* score, so it
# reads as maximally unstable exactly where there is least to think about,
# while a sharp position the engine happens to see clearly reads as calm.
# Move-changes are a poor difficulty signal for this engine; a collapsing
# score is a good one. Anything built here should extend the latter.
PANIC_SCORE_DROP = 60

# Aspiration windows: from this depth on, re-search around the previous
# iteration's score inside a narrow window instead of the full one. Most
# searches stay inside it, so alpha-beta prunes far more; the rare fail-high /
# fail-low widens the window (doubling from DELTA) and re-searches, falling back
# to a full window once it grows past MAX.
ASPIRATION_MIN_DEPTH = 3
ASPIRATION_DELTA = 50
ASPIRATION_MAX = 1000

# Static exchange evaluation (SEE): the king gets a huge notional value so the
# swap-off never "wins" by marching the king into a defended square (which would
# be an illegal capture). The direction tuples drive the attacker scan.
_SEE_KING_VALUE = 10_000
_SEE_DIAGONALS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_SEE_ORTHOGONALS = ((-1, 0), (1, 0), (0, -1), (0, 1))
_SEE_KNIGHT_HOPS = (
    (-2, -1), (-2, 1), (-1, -2), (-1, 2),
    (1, -2), (1, 2), (2, -1), (2, 1),
)

# The same precomputed-geometry trick the mobility scan uses (see
# `_MOBILITY_RAYS`): the attacker scan walks these rays for every square of
# every exchange it evaluates, and the squares are fixed by the board, not by
# the position. `_least_valuable_attacker` never needs the direction it walked
# — only the piece it found and how far away it was — so it can iterate the
# rays directly.
_SEE_DIAGONAL_RAYS: tuple[tuple[tuple[tuple[tuple[int, int], ...], ...], ...], ...] = tuple(
    tuple(_rays_from(row, col, _SEE_DIAGONALS) for col in range(8))
    for row in range(8)
)
_SEE_ORTHOGONAL_RAYS: tuple[tuple[tuple[tuple[tuple[int, int], ...], ...], ...], ...] = tuple(
    tuple(_rays_from(row, col, _SEE_ORTHOGONALS) for col in range(8))
    for row in range(8)
)

_root_rng = random.Random()

# Monotonic counter stamped onto every TT entry so a game-long table can tell
# the current search's entries from stale ones left by previous moves. Bumped
# once per `search_position` call.
_search_generation = 0

# Nodes visited by the most recent `search_position` call. Published because
# node count is the one search statistic that is *perfectly deterministic*:
# given the same position, depth and root move order, two versions of this
# engine visit the same nodes unless their pruning or ordering actually
# differs. That makes it the right yardstick for changes meant to be
# score-neutral (see `engine.bench`) — a self-play Elo match cannot resolve
# anything under ~35 Elo without hundreds of games, while a node count
# resolves a 1% difference in one run.
last_search_nodes = 0


class SearchTimeout(Exception):
    """Raised internally when the soft time limit expires mid-search."""


class SearchInfo:
    """
    Mutable per-search bookkeeping shared across the recursion.

    Attributes
    ----------
    start_time : float
        Monotonic timestamp when the search began.
    time_limit : float
        Soft limit in seconds; the search aborts the current iteration when hit.
    nodes : int
        Number of nodes visited (search statistics / time-check pacing).
    tt : TTable
        Transposition table: zobrist_key -> (depth, flag, score, best_move,
        generation). Freshly built per search unless the caller passes a shared
        table in, in which case results persist across searches (the
        UCI adapter reuses one table for a whole game, so each move's search
        starts warm from the previous moves' work).
    generation : int
        This search's stamp for TT entries it writes; lets the replacement
        policy evict entries left over from earlier moves of the game.
    killers : list of list of MoveTuple
        Two killer moves (quiet beta-cutoff moves) per ply.
    history : dict of tuple to int
        History heuristic scores for quiet move ordering.
    rep_counts : dict of int, int
        Occurrence counts of Zobrist keys along game history + search path.
    aborted : bool
        Set when the clock cut an iteration short. The root keeps whatever it
        had already proved at that depth (see `_search_root`), so callers use
        this flag rather than an exception to tell "finished" from "ran out".
    """

    def __init__(self, time_limit: float, rep_counts: dict[int, int],
                 tt: TTable | None = None, generation: int = 0) -> None:
        self.start_time: float = time.perf_counter()
        self.time_limit: float = time_limit
        self.nodes: int = 0
        self.tt: TTable = {} if tt is None else tt
        self.generation: int = generation
        self.killers: list[list[MoveTuple]] = [[] for _ in range(64)]
        self.history: dict[tuple[int, int, int, int], int] = {}
        self.rep_counts: dict[int, int] = rep_counts
        self.aborted: bool = False

    def check_time(self) -> None:
        """Raise SearchTimeout if the soft time limit has expired."""
        # Sampling the clock costs a syscall, so it happens every N nodes
        # rather than every node — which means the search can overrun its
        # limit by however long N nodes take. That error is near-constant in
        # *time*, so it barely matters against a 5s budget and matters a lot
        # against the 0.05s one a nearly-flagged clock produces: at 2048 nodes
        # (~58ms here) a 0.1s budget overran by 41%. 512 quarters that for a
        # cost of 0.6% on the benchmark — inside its noise, and the node count
        # is unchanged, which proves the search itself is untouched.
        if self.nodes % 512 == 0:
            if time.perf_counter() - self.start_time > self.time_limit:
                raise SearchTimeout


def find_best_move(
    gs: GameState,
    valid_moves: list[MoveTuple] | None = None,
    max_depth: int = 4,
    time_limit: float = 5.0,
    tt: TTable | None = None,
    hard_limit: float | None = None,
    on_iteration: Callable[[int, int, int, float, MoveTuple], None] | None = None,
) -> MoveTuple | None:
    """
    Entry point for the AI: search the position and return the best move.

    Runs iterative deepening from depth 1 to `max_depth`, keeping the best
    move from the last fully completed iteration if the time limit interrupts
    a deeper one. The GameState is fully restored before returning.

    Parameters
    ----------
    gs : GameState
        The current game state object (mutated during search, then restored).
    valid_moves : list of MoveTuple, optional
        Pre-calculated legal AI move tuples for the root position. Generated
        on demand when omitted.
    max_depth : int, optional
        Maximum iterative-deepening depth. Default is 4.
    time_limit : float, optional
        Soft time limit in seconds. Default is 5.0.
    tt : TTable, optional
        A transposition table to reuse and fill. Passing the same dict for
        every move of a game lets each search start from the previous
        searches' results. Omitted, each search builds its own.
    hard_limit : float, optional
        Absolute ceiling in seconds for the panic extension (see
        `search_position`). Omitted, the soft limit is also the ceiling.
    on_iteration : callable, optional
        Per-iteration progress callback; see `search_position`.

    Returns
    -------
    MoveTuple or None
        The best move tuple found, or None if the position has no legal moves.
    """
    return search_position(gs, valid_moves, max_depth, time_limit, tt,
                           hard_limit, on_iteration)[0]


def search_position(
    gs: GameState,
    valid_moves: list[MoveTuple] | None = None,
    max_depth: int = 4,
    time_limit: float = 5.0,
    tt: TTable | None = None,
    hard_limit: float | None = None,
    on_iteration: Callable[[int, int, int, float, MoveTuple], None] | None = None,
) -> tuple[MoveTuple | None, int]:
    """
    Search the position and return both the best move and its score.

    This is the same iterative-deepening search as `find_best_move`, but it
    also reports the score of the last completed iteration — the extra piece
    of information the game-review feature needs to draw the evaluation bar
    and grade move quality.

    Parameters
    ----------
    gs : GameState
        The current game state object (mutated during search, then restored).
    valid_moves : list of MoveTuple, optional
        Pre-calculated legal AI move tuples for the root position. Passing a
        subset of the legal moves restricts the search to those moves, which
        the review feature uses to score the "second best" alternative.
    max_depth : int, optional
        Maximum iterative-deepening depth. Default is 4.
    time_limit : float, optional
        Soft time limit in seconds. Default is 5.0.
    tt : TTable, optional
        A transposition table to reuse and fill across searches (see
        `find_best_move`). Omitted, each search builds its own.
    hard_limit : float, optional
        Absolute time ceiling in seconds for the *panic extension*: when an
        iteration's score collapses (see PANIC_SCORE_DROP), the search may
        keep thinking past the soft limit up to this ceiling. Omitted or
        not above `time_limit`, panic changes nothing — old behavior.
    on_iteration : callable, optional
        Called after every completed deepening iteration with
        ``(depth, score, nodes, elapsed_seconds, best_move)``. This is how
        the UCI adapter emits `info` lines: the search itself must never
        print, because it also runs inside the GUI where stdout is not a
        protocol channel. Reporting is the caller's business.

    Returns
    -------
    tuple of (MoveTuple or None, int)
        The best move found and its score in centipawns from the side to
        move's perspective. With no legal moves the move is None and the
        score is a mate/draw score for the terminal position.
    """
    if valid_moves is None:
        valid_moves = gs.get_valid_moves(for_ai=True)
    if not valid_moves:
        # Terminal position: mated (score from the loser's perspective) or
        # stalemated. Mirrors the scoring inside _negamax at ply 0.
        return None, (-CHECKMATE_SCORE if gs.in_check else DRAW_SCORE)

    # Seed repetition detection with the real game history so the engine
    # recognizes (and can aim for or avoid) threefold repetitions
    rep_counts: dict[int, int] = {}
    for key in getattr(gs, 'zobrist_history', [gs.zobrist_key]):
        rep_counts[key] = rep_counts.get(key, 0) + 1

    global _search_generation
    _search_generation += 1
    # The in-search abort limit starts at the soft budget; only a panic
    # (below) moves it out to the hard ceiling.
    hard = hard_limit if hard_limit is not None and hard_limit > time_limit else time_limit
    info = SearchInfo(time_limit, rep_counts, tt, _search_generation)

    # Shuffle once so equal-scoring moves vary between games
    root_moves = list(valid_moves)
    _root_rng.shuffle(root_moves)

    # A forced move needs no deliberation: play it after one cheap iteration
    # (which still produces a sane score for the review feature) and bank
    # the entire budget for a move where thinking can change something.
    if len(root_moves) == 1:
        max_depth = 1

    best_move: MoveTuple | None = root_moves[0]
    best_score = -CHECKMATE_SCORE
    prev_score: int | None = None
    panic = False

    for depth in range(1, max_depth + 1):
        # Soft stop: never abandon depth 1 (a move must exist), but don't
        # start a deeper iteration that the remaining budget can't finish —
        # see SOFT_STOP_FRACTION. A panic widens the gate to the full soft
        # budget. The in-search abort (at the soft limit normally, the hard
        # ceiling during a panic) stays as backstop.
        gate = time_limit if panic else time_limit * SOFT_STOP_FRACTION
        if depth > 1 and time.perf_counter() - info.start_time > gate:
            break
        try:
            score, move = _aspiration_search(gs, root_moves, depth, info, best_score)
        except SearchTimeout:
            # Safety net only: the root converts a timeout into `info.aborted`
            # and returns what it proved. Reaching here means a timeout
            # escaped from somewhere outside the root's move loop.
            break

        if move is not None:
            best_move, best_score = move, score
            # Re-order the root list so the current best is searched first
            root_moves.remove(move)
            root_moves.insert(0, move)
            # Panic check: this iteration saw the score collapse relative to
            # the previous one — the classic signature of a tactic spotted
            # one ply too late. Re-checked every iteration, so a recovered
            # score calms the search back down.
            panic = (prev_score is not None
                     and score < prev_score - PANIC_SCORE_DROP
                     and abs(score) < MATE_THRESHOLD)
            info.time_limit = hard if panic else time_limit
            prev_score = score

            if on_iteration is not None:
                on_iteration(depth, score, info.nodes,
                             time.perf_counter() - info.start_time, move)

        # The clock cut this iteration short. Its partial result has already
        # been taken above, but there is no time for another one.
        if info.aborted:
            break

        # A forced mate found: deeper search cannot improve it
        if abs(best_score) >= MATE_THRESHOLD:
            break

    global last_search_nodes
    last_search_nodes = info.nodes
    return best_move, best_score


def _search_root(
    gs: GameState,
    root_moves: list[MoveTuple],
    depth: int,
    info: SearchInfo,
    alpha: int,
    beta: int,
) -> tuple[int, MoveTuple | None]:
    """
    Search all root moves at a fixed depth and return (score, best_move).

    Fail-soft within the given window: the returned score is the true best
    among the root moves even when it lands outside ``[alpha, beta]``. A score
    ``<= alpha`` (fail low) or ``>= beta`` (fail high) tells the aspiration
    driver to widen the window and re-search; ``best_move`` is still the
    best-scoring move so the re-search searches it first.

    Parameters
    ----------
    gs : GameState
        The game state positioned at the search root.
    root_moves : list of MoveTuple
        Legal root moves, pre-ordered (best move from previous iteration first).
    depth : int
        The nominal search depth for this iteration.
    info : SearchInfo
        Shared search bookkeeping.
    alpha, beta : int
        The search window. Pass the full ``[-CHECKMATE_SCORE, CHECKMATE_SCORE]``
        for a normal (non-aspiration) iteration.

    Returns
    -------
    tuple
        (best_score, best_move) from the side to move's perspective.
    """
    best_score = -CHECKMATE_SCORE
    best_move: MoveTuple | None = None
    score: int | None

    for move in root_moves:
        undo = gs.make_ai_move(move)
        child_key = gs.zobrist_key
        info.rep_counts[child_key] = info.rep_counts.get(child_key, 0) + 1
        try:
            # Principal variation search at the root: only the first move (the
            # previous iteration's best, thanks to the pre-ordering) gets the
            # full window. Every later move is first *scouted* with a null
            # window — "prove you are worse than the best so far" is a much
            # cheaper question than "what is your exact score" — and only a
            # scout that beats alpha earns the full-window re-search.
            if best_move is None:
                score = -_negamax(gs, depth - 1, -beta, -alpha, 1, info)
            else:
                score = -_negamax(gs, depth - 1, -alpha - 1, -alpha, 1, info)
                if alpha < score < beta:
                    score = -_negamax(gs, depth - 1, -beta, -alpha, 1, info)
        except SearchTimeout:
            # The clock ran out inside this move. Everything proved *before*
            # it is still valid, so record the abort and keep it rather than
            # discarding the whole iteration. Root moves are ordered
            # best-first from the previous iteration, so a partial pass has
            # already examined the most promising candidates: it either
            # confirms the old best or replaces it with something that
            # outscored it at this deeper depth. Both are strictly better
            # information than the previous iteration alone.
            info.aborted = True
            score = None
        finally:
            info.rep_counts[child_key] -= 1
            gs.unmake_ai_move(move, undo)

        if score is None:
            break

        if score > best_score:
            best_score = score
            best_move = move
        if score > alpha:
            alpha = score
        # Fail high: this move already beats the window, no need to look further
        if alpha >= beta:
            break

    return best_score, best_move


def _aspiration_search(
    gs: GameState,
    root_moves: list[MoveTuple],
    depth: int,
    info: SearchInfo,
    prev_score: int,
) -> tuple[int, MoveTuple | None]:
    """
    Run one iterative-deepening iteration, narrowing the root window.

    From ``ASPIRATION_MIN_DEPTH`` on, the search opens a narrow window centred
    on the previous iteration's score. If the true score falls inside, that one
    narrow search is far cheaper than a full-window one; if it fails high or
    low, the window doubles and the depth is re-searched, reverting to a full
    window once it grows past ``ASPIRATION_MAX``. Shallow depths and near-mate
    scores skip straight to the full window (no reliable centre to bet on).

    Parameters
    ----------
    gs : GameState
        The game state positioned at the search root.
    root_moves : list of MoveTuple
        Legal root moves, best-first from the previous iteration.
    depth : int
        The nominal search depth for this iteration.
    info : SearchInfo
        Shared search bookkeeping.
    prev_score : int
        The score of the previous completed iteration (the window's centre).

    Returns
    -------
    tuple
        (best_score, best_move) from the side to move's perspective.
    """
    full = (-CHECKMATE_SCORE, CHECKMATE_SCORE)
    if depth < ASPIRATION_MIN_DEPTH or abs(prev_score) >= MATE_THRESHOLD:
        return _search_root(gs, root_moves, depth, info, *full)

    window = ASPIRATION_DELTA
    while True:
        alpha = prev_score - window
        beta = prev_score + window
        score, move = _search_root(gs, root_moves, depth, info, alpha, beta)
        # Out of time: a re-search cannot finish either, so take what the
        # aborted pass proved instead of throwing it away on a widen.
        if info.aborted:
            return score, move
        if alpha < score < beta:
            return score, move
        # Fail high or low: widen and re-search, or give up and go full-window
        window *= 2
        if window > ASPIRATION_MAX:
            return _search_root(gs, root_moves, depth, info, *full)


def _negamax(
    gs: GameState,
    depth: int,
    alpha: int,
    beta: int,
    ply: int,
    info: SearchInfo,
) -> int:
    """
    Recursive negamax core with alpha-beta pruning and a transposition table.

    Parameters
    ----------
    gs : GameState
        The game state at the current node (mutated and restored in place).
    depth : int
        Remaining search depth in plies.
    alpha : int
        Lower bound of the search window.
    beta : int
        Upper bound of the search window.
    ply : int
        Distance from the search root (used for mate scoring and killers).
    info : SearchInfo
        Shared search bookkeeping.

    Returns
    -------
    int
        The score of the position from the side to move's perspective.
    """
    info.nodes += 1
    info.check_time()

    key = gs.zobrist_key

    # Twofold repetition along the game history or search path scores a draw
    if info.rep_counts.get(key, 0) >= 2:
        return DRAW_SCORE

    # Transposition table probe
    tt_move: MoveTuple | None = None
    entry = info.tt.get(key)
    if entry is not None:
        tt_depth, tt_flag, tt_score, tt_move, _tt_gen = entry
        if tt_depth >= depth:
            if tt_flag == TT_EXACT:
                return tt_score
            if tt_flag == TT_LOWER and tt_score >= beta:
                return tt_score
            if tt_flag == TT_UPPER and tt_score <= alpha:
                return tt_score

    if depth <= 0:
        return _quiescence(gs, alpha, beta, ply, info)

    moves = gs.get_valid_moves(for_ai=True)
    in_check = gs.in_check

    if not moves:
        # Prefer faster mates: a mate further from the root scores less
        return -(CHECKMATE_SCORE - ply) if in_check else DRAW_SCORE

    # Check extension: never stand at depth 0 while in check
    if in_check:
        depth += 1

    # One static eval serves both futility prunes below. Only computed near
    # the leaves and never while in check (a checked position's static score
    # is meaningless — the whole point of the check extension above).
    static_eval: int | None = None
    if depth <= max(RFP_MAX_DEPTH, FUTILITY_MAX_DEPTH) and not in_check:
        static_eval = evaluate(gs) if gs.white_to_move else -evaluate(gs)

    # Reverse futility pruning: standing far enough above beta that no
    # reply within this depth plausibly drags us back below it.
    if (static_eval is not None and depth <= RFP_MAX_DEPTH
            and beta < MATE_THRESHOLD
            and static_eval - RFP_MARGIN * depth >= beta):
        return static_eval - RFP_MARGIN * depth

    # Null-move pruning: if skipping our turn still fails high, prune.
    # Avoided in check and in pawn-only endings where zugzwang is common.
    if depth >= 3 and not in_check and beta < MATE_THRESHOLD and _has_major_material(gs):
        null_undo = gs.make_null_move()
        try:
            null_score = -_negamax(gs, depth - 1 - NULL_MOVE_REDUCTION, -beta, -beta + 1, ply + 1, info)
        finally:
            gs.unmake_null_move(null_undo)
        if null_score >= beta:
            return beta

    ordered = _order_moves(gs, moves, tt_move, info, ply)

    # Frontier futility applies when the static eval sits hopelessly below
    # alpha at shallow depth; quiet moves are then skipped inside the loop.
    # Mate-score windows are exempt: when hunting a mate, "hopeless" material
    # arithmetic doesn't apply. The margin-padded score is fixed here; alpha
    # only rises during the loop, so the in-loop comparison only gets stricter.
    futility_score: int | None = None
    if (static_eval is not None and depth <= FUTILITY_MAX_DEPTH
            and abs(alpha) < MATE_THRESHOLD):
        futility_score = static_eval + FUTILITY_MARGIN[depth]

    original_alpha = alpha
    best_score = -CHECKMATE_SCORE
    best_move: MoveTuple | None = None

    for move_index, move in enumerate(ordered):
        undo = gs.make_ai_move(move)
        # A "quiet" move: no capture and no promotion. Whether it also *gives
        # check* is asked separately, and only where the answer matters — the
        # test costs an attack scan. It cannot use `gs.in_check`: that flag is
        # only refreshed by get_valid_moves(), so straight after
        # make_ai_move() it still describes the parent position (it equals the
        # `in_check` local above) and would silently never fire.
        quiet = undo[0] == EMPTY and move[4] < 3
        # Skip a futile quiet move — but only after at least one move has been
        # fully searched (so a real score always exists), and never one that
        # captures, promotes, or gives check. This skip is unrecoverable, so
        # it pays for the scan.
        if (futility_score is not None and best_move is not None
                and futility_score <= alpha
                and quiet and not gs.side_to_move_in_check()):
            gs.unmake_ai_move(move, undo)
            continue
        child_key = gs.zobrist_key
        info.rep_counts[child_key] = info.rep_counts.get(child_key, 0) + 1
        try:
            # Principal variation search: the first move — the TT/ordering
            # favourite — is searched with the full window and becomes the
            # standard every sibling must beat. Each later move is *scouted*
            # with a null window, which asks the much cheaper question "are
            # you worse than the best so far?"; with good move ordering the
            # answer is almost always yes, and the scout's refutation subtree
            # stays tiny. Only a scout that beats alpha earns the full-window
            # re-search that establishes its exact score.
            #
            # Late move reduction rides on the same scout: a quiet move
            # ordered late (not a capture, promotion, killer, and not while we
            # are in check) is unlikely to be best, so its scout runs
            # shallower — by the log-scaled amount from LMR_TABLE, minus one
            # ply for a move whose history record has earned it a closer look,
            # and never into the quiescence boundary (the scout keeps at least
            # one full ply).
            #
            # Unlike the futility skip above, this deliberately does not pay
            # for a check test. The two errors are not symmetrical: a wrongly
            # reduced move is still searched, and if its shallow scout beats
            # alpha it is re-searched at full depth, so the mistake costs time
            # and self-corrects. A wrongly skipped move is never searched at
            # all — only that is worth an attack scan per candidate move.
            reduction = 0
            if (depth >= LMR_MIN_DEPTH
                    and move_index >= LMR_MIN_MOVE_INDEX
                    and quiet
                    and not in_check
                    and move not in info.killers[min(ply, 63)]):
                reduction = LMR_TABLE[min(depth, 63)][min(move_index, 63)]
                if reduction and info.history.get(
                        (move[0], move[1], move[2], move[3]), 0) >= HISTORY_EXEMPT_SCORE:
                    reduction -= 1
                reduction = min(reduction, depth - 2)
            if best_move is None:
                score = -_negamax(gs, depth - 1, -beta, -alpha, ply + 1, info)
            else:
                score = -_negamax(gs, depth - 1 - reduction, -alpha - 1, -alpha, ply + 1, info)
                # A reduced scout that beats alpha must be re-searched at
                # full depth even when it also beats beta — a cutoff claimed
                # from a shallower search is not trustworthy. An unreduced
                # scout that fails high (>= beta) already is a full-depth
                # lower bound, so the cutoff below can take it as is.
                if score > alpha and (reduction or score < beta):
                    score = -_negamax(gs, depth - 1, -beta, -alpha, ply + 1, info)
        finally:
            info.rep_counts[child_key] -= 1
            gs.unmake_ai_move(move, undo)

        if score > best_score:
            best_score = score
            best_move = move

        if best_score > alpha:
            alpha = best_score

        if alpha >= beta:
            # Beta cutoff: reward quiet moves via killer/history heuristics
            if quiet:
                killers = info.killers[min(ply, 63)]
                if move not in killers:
                    killers.insert(0, move)
                    del killers[2:]
                hist_key = (move[0], move[1], move[2], move[3])
                info.history[hist_key] = info.history.get(hist_key, 0) + depth * depth
            break

    # Store in the transposition table (mate scores excluded: they are
    # ply-relative and would corrupt entries reached at different plies)
    if abs(best_score) < MATE_THRESHOLD:
        if best_score <= original_alpha:
            flag = TT_UPPER
        elif best_score >= beta:
            flag = TT_LOWER
        else:
            flag = TT_EXACT
        # Depth-preferred replacement with aging: keep a deeper result from the
        # current search, but always overwrite an entry left by an earlier move
        # (a stale generation) so a game-long table stays fresh rather than
        # pinning shallow results from positions we will never revisit.
        existing = info.tt.get(key)
        if (existing is None or existing[4] != info.generation
                or depth >= existing[0]):
            info.tt[key] = (depth, flag, best_score, best_move, info.generation)

    return best_score


def _quiescence(gs: GameState, alpha: int, beta: int, ply: int, info: SearchInfo) -> int:
    """
    Search only captures and promotions until the position is "quiet".

    Prevents the horizon effect: without this, a fixed-depth search would
    happily stop in the middle of a queen trade and misjudge the position.

    Parameters
    ----------
    gs : GameState
        The game state at the current node.
    alpha : int
        Lower bound of the search window.
    beta : int
        Upper bound of the search window.
    ply : int
        Distance from the search root.
    info : SearchInfo
        Shared search bookkeeping.

    Returns
    -------
    int
        The quiet score of the position from the side to move's perspective.
    """
    info.nodes += 1
    info.check_time()

    # Transposition-table probe (search-review stage G): quiescence visits
    # the bulk of all nodes, and many were already settled — by a real
    # depth >= 1 search of this position, or by an earlier quiescence pass.
    # Every stored entry's depth is at least the 0 this node searches at,
    # so any stored bound that closes the window answers immediately.
    key = gs.zobrist_key
    entry = info.tt.get(key)
    if entry is not None:
        _tt_depth, tt_flag, tt_score, _tt_move, _tt_gen = entry
        if tt_flag == TT_EXACT:
            return tt_score
        if tt_flag == TT_LOWER and tt_score >= beta:
            return tt_score
        if tt_flag == TT_UPPER and tt_score <= alpha:
            return tt_score

    turn = 1 if gs.white_to_move else -1
    stand_pat = turn * evaluate(gs)

    if stand_pat >= beta:
        return beta
    if stand_pat > alpha:
        alpha = stand_pat

    # Captures-only generation: quiet moves
    # are never materialized, which matters because the bulk of all visited
    # nodes are quiescence nodes. In check the generator returns the complete
    # evasion list instead, so an empty result there is a real checkmate —
    # while an empty captures-only list simply means the position is quiet
    # and the stand-pat score above already bounds it.
    noisy = gs.get_valid_moves(for_ai=True, captures_only=True)
    if not noisy:
        return -(CHECKMATE_SCORE - ply) if gs.in_check else alpha

    noisy.sort(key=lambda m: _mvv_lva(gs, m), reverse=True)

    original_alpha = alpha
    best_move: MoveTuple | None = None

    in_check = gs.in_check
    for move in noisy:
        # Prune captures that lose material by static exchange: they cannot
        # raise alpha and only cost nodes. Never prune while in check (these are
        # forced evasions, not optional captures) or for promotions (move types
        # >= 3), whose +queen swing SEE does not account for.
        if not in_check and move[4] < 3 and _see(gs, move) < 0:
            continue
        # Delta pruning (stage G): even winning the victim outright plus a
        # safety margin can't lift this line back to alpha — skip the capture
        # before paying for make/unmake and a child search. Same exemptions
        # as the SEE prune, plus mate-score windows, where material
        # arithmetic means nothing.
        if not in_check and move[4] < 3 and abs(alpha) < MATE_THRESHOLD:
            # En passant is the one capture whose victim is not on the
            # target square; every other move[4] < 3 capture's is.
            victim = PIECE_VALUES[WP] if move[4] == 2 \
                else PIECE_VALUES[gs.board[move[2]][move[3]]]
            if stand_pat + victim + DELTA_MARGIN <= alpha:
                continue
        undo = gs.make_ai_move(move)
        try:
            score = -_quiescence(gs, -beta, -alpha, ply + 1, info)
        finally:
            gs.unmake_ai_move(move, undo)

        if score >= beta:
            # Store the fail-high as a depth-0 lower bound (stage G), under
            # the same replacement rule as the main search: never displace a
            # deeper entry from the current generation.
            if abs(beta) < MATE_THRESHOLD:
                existing = info.tt.get(key)
                if (existing is None or existing[4] != info.generation
                        or existing[0] <= 0):
                    info.tt[key] = (0, TT_LOWER, beta, move, info.generation)
            return beta
        if score > alpha:
            alpha = score
            best_move = move

    # Store the settled result (stage G): an exact score if some capture
    # raised alpha, otherwise an upper bound. Mate scores stay out of the
    # table (ply-relative), matching the main search.
    if abs(alpha) < MATE_THRESHOLD:
        flag = TT_EXACT if alpha > original_alpha else TT_UPPER
        existing = info.tt.get(key)
        if (existing is None or existing[4] != info.generation
                or existing[0] <= 0):
            info.tt[key] = (0, flag, alpha, best_move, info.generation)
    return alpha


def _order_moves(
    gs: GameState,
    moves: list[MoveTuple],
    tt_move: MoveTuple | None,
    info: SearchInfo,
    ply: int,
) -> list[MoveTuple]:
    """
    Sort moves so the most promising are searched first.

    Ordering priority: TT best move, then captures by MVV-LVA (Most Valuable
    Victim - Least Valuable Attacker), then promotions, then killer moves,
    then quiet moves by history heuristic.

    Parameters
    ----------
    gs : GameState
        The game state at the current node.
    moves : list of MoveTuple
        The legal moves to order.
    tt_move : MoveTuple or None
        The best move recorded in the transposition table, if any.
    info : SearchInfo
        Shared search bookkeeping (killers/history tables).
    ply : int
        Distance from the search root (selects the killer slot).

    Returns
    -------
    list of MoveTuple
        The same moves, sorted best-first.
    """
    board = gs.board
    killers = info.killers[min(ply, 63)]
    history = info.history

    def score(move: MoveTuple) -> int:
        if move == tt_move:
            return 2_000_000
        victim = board[move[2]][move[3]]
        if victim != EMPTY or move[4] == 2:
            # Winning/equal captures sort by MVV-LVA up top. For captures where
            # the attacker outweighs the victim (a possible losing trade), pay
            # for a SEE check and, if it really loses, drop the move below the
            # killers so quiet moves get tried first.
            attacker_value = PIECE_VALUES[board[move[0]][move[1]]]
            victim_value = (PIECE_VALUES[WP] if move[4] == 2
                            else PIECE_VALUES[victim])
            if attacker_value > victim_value:
                see = _see(gs, move)
                if see < 0:
                    return 300_000 + see
            return 1_000_000 + _mvv_lva(gs, move)
        if move[4] >= 3:  # Quiet promotions
            return 900_000 + PIECE_VALUES[AI_PROMO_TYPE[move[4]]]
        if move in killers:
            return 800_000
        return history.get((move[0], move[1], move[2], move[3]), 0)

    return sorted(moves, key=score, reverse=True)


def _mvv_lva(gs: GameState, move: MoveTuple) -> int:
    """
    Score a capture by Most Valuable Victim - Least Valuable Attacker.

    Parameters
    ----------
    gs : GameState
        The game state providing board piece lookups.
    move : MoveTuple
        The capture (or promotion) move to score.

    Returns
    -------
    int
        A heuristic ordering score; higher means try earlier.
    """
    board = gs.board
    victim = board[move[2]][move[3]]
    victim_value = PIECE_VALUES[WP] if move[4] == 2 else (
        PIECE_VALUES[victim] if victim != EMPTY else 0
    )
    attacker_value = PIECE_VALUES[board[move[0]][move[1]]]
    promo_bonus = PIECE_VALUES[AI_PROMO_TYPE[move[4]]] if move[4] >= 3 else 0
    return victim_value * 10 - attacker_value + promo_bonus


def _first_on_ray(
    board: list[list[int]], ray: tuple[tuple[int, int], ...],
    removed: set[tuple[int, int]],
) -> tuple[int, int, int] | None:
    """
    Walk a precomputed ray and return the first live piece on it.

    Squares in ``removed`` (pieces already spent in the exchange) are treated
    as empty, so a slider standing behind one of them is revealed — this is how
    the SEE swap picks up X-ray attackers as the front pieces come off.

    Parameters
    ----------
    board : list of list of int
        The board grid of integer piece codes.
    ray : tuple of (row, col)
        On-board squares walking outward from the target, from
        ``_SEE_DIAGONAL_RAYS`` / ``_SEE_ORTHOGONAL_RAYS``. Being precomputed,
        every square is known to be on the board, so the walk needs no bounds
        test.
    removed : set of (row, col)
        Squares whose pieces have already been spent in the exchange.

    Returns
    -------
    tuple of (row, col, piece int) or None
        The first occupied, non-removed square along the ray, or None if the
        ray runs out without hitting one.
    """
    for r, c in ray:
        if (r, c) not in removed and board[r][c] != EMPTY:
            return r, c, board[r][c]
    return None


def _least_valuable_attacker(
    board: list[list[int]], tr: int, tc: int, side_white: bool,
    removed: set[tuple[int, int]],
) -> tuple[int, tuple[int, int]] | None:
    """
    Find the side's cheapest piece currently attacking square (tr, tc).

    Used by the SEE swap loop to decide who recaptures next. Checks piece types
    in ascending value order (pawn, knight, bishop, rook, queen, king) so the
    first hit is always the least valuable attacker. Squares in ``removed`` are
    ignored, which both drops spent attackers and reveals X-ray attackers.

    Parameters
    ----------
    side_white : bool
        True to look for a White attacker, False for a Black one.

    Returns
    -------
    tuple of (value, (row, col)) or None
        The attacker's SEE value and square, or None if the side has no
        remaining attacker on the target.
    """
    # Pawns: a side pawn attacks from one rank behind, on a neighbouring file.
    # White pawns capture toward row 0, so a white attacker sits on row tr + 1.
    pawn = WP if side_white else BP
    pr = tr + 1 if side_white else tr - 1
    if 0 <= pr < 8:
        for pc in (tc - 1, tc + 1):
            if 0 <= pc < 8 and (pr, pc) not in removed and board[pr][pc] == pawn:
                return PIECE_VALUES[WP], (pr, pc)

    # Knights: cheaper than any slider, so return as soon as one is found.
    knight = WN if side_white else BN
    for dr, dc in _SEE_KNIGHT_HOPS:
        r, c = tr + dr, tc + dc
        if 0 <= r < 8 and 0 <= c < 8 and (r, c) not in removed and board[r][c] == knight:
            return PIECE_VALUES[WN], (r, c)

    # Sliders and the king: the first live piece on each ray is the only one
    # that can attack along it (anything behind is blocked until it is removed).
    bishop = rook = queen = king = None
    for ray in _SEE_DIAGONAL_RAYS[tr][tc]:
        found = _first_on_ray(board, ray, removed)
        if found is None or (found[2] < BP) != side_white:
            continue
        r, c, kind = found[0], found[1], PIECE_TYPE[found[2]]
        if kind == BISHOP:
            bishop = bishop or (r, c)
        elif kind == QUEEN:
            queen = queen or (r, c)
        elif kind == KING and abs(r - tr) <= 1 and abs(c - tc) <= 1:
            king = king or (r, c)
    for ray in _SEE_ORTHOGONAL_RAYS[tr][tc]:
        found = _first_on_ray(board, ray, removed)
        if found is None or (found[2] < BP) != side_white:
            continue
        r, c, kind = found[0], found[1], PIECE_TYPE[found[2]]
        if kind == ROOK:
            rook = rook or (r, c)
        elif kind == QUEEN:
            queen = queen or (r, c)
        elif kind == KING and abs(r - tr) <= 1 and abs(c - tc) <= 1:
            king = king or (r, c)

    if bishop is not None:
        return PIECE_VALUES[WB], bishop
    if rook is not None:
        return PIECE_VALUES[WR], rook
    if queen is not None:
        return PIECE_VALUES[WQ], queen
    if king is not None:
        return _SEE_KING_VALUE, king
    return None


def _see(gs: GameState, move: MoveTuple) -> int:
    """
    Static exchange evaluation: the material a capture nets after the full swap.

    Plays out the capture on the target square and every recapture by both
    sides, always with the least valuable attacker, then minimaxes the gain
    stack. A negative result means the capture loses material once the opponent
    replies — the search uses that to prune and de-prioritise losing captures.

    Only defined for ordinary captures and en passant (move types 0 and 2);
    promotions are scored elsewhere and must not be passed here.

    Parameters
    ----------
    gs : GameState
        The game state providing the board.
    move : MoveTuple
        The capture to evaluate.

    Returns
    -------
    int
        Net material for the side to move, in centipawns (positive is good).
    """
    board = gs.board
    sr, sc, er, ec, mt = move
    mover = board[sr][sc]
    if mt == 2:  # En passant: the captured pawn sits beside the target square
        captured_value = PIECE_VALUES[WP]
        removed = {(sr, sc), (sr, ec)}
    else:
        victim = board[er][ec]
        if victim == EMPTY:
            return 0  # Not a capture; SEE is undefined, treat as neutral
        captured_value = PIECE_VALUES[victim]
        removed = {(sr, sc)}

    # gain[d] is the material the side to move at depth d stands to win if the
    # exchange stops there; attacker_value is the piece now sitting on the
    # target, exposed to the next recapture.
    gain = [captured_value]
    attacker_value = _SEE_KING_VALUE if PIECE_TYPE[mover] == KING else PIECE_VALUES[mover]
    side_white = mover >= BP  # the opponent of the mover recaptures first

    depth = 0
    while True:
        lva = _least_valuable_attacker(board, er, ec, side_white, removed)
        if lva is None:
            break
        depth += 1
        gain.append(attacker_value - gain[depth - 1])
        # If the side to move can't come out ahead even in the best case, deeper
        # recaptures can't flip the result — stop early.
        if max(-gain[depth - 1], gain[depth]) < 0:
            break
        attacker_value, lva_sq = lva
        removed.add(lva_sq)
        side_white = not side_white

    # Minimax the speculative gains back to the root: at each level the side to
    # move only recaptures if it does not worsen their result.
    while depth > 0:
        gain[depth - 1] = -max(-gain[depth - 1], gain[depth])
        depth -= 1
    return gain[0]


def _has_major_material(gs: GameState) -> bool:
    """
    Check whether the side to move still owns a non-pawn, non-king piece.

    Null-move pruning is unsound in pawn-only endings (zugzwang positions),
    so the search disables it when this returns False.
    """
    board = gs.board
    pieces = gs.white_pieces if gs.white_to_move else gs.black_pieces
    for row, col in pieces:
        if PIECE_TYPE[board[row][col]] not in (PAWN, KING):
            return True
    return False


def evaluate(gs: GameState) -> int:
    """
    Static evaluation of the position from White's perspective.

    Combines raw material with piece-square table bonuses, plus the
    positional refinements — a bishop-pair bonus, passed-pawn bonuses that grow as the
    pawn advances, a king pawn-shield bonus, and a hard zero for positions
    where neither side has enough material to mate (so the bot offers/
    accepts draws sensibly online) — plus rook bonuses on
    semi-open/open files and the 7th rank, and doubled/isolated pawn
    penalties. Phase-sensitive terms (king tables, passed pawns, the pawn
    shield) are *tapered*: blended between middlegame and endgame values by
    the fraction of non-pawn material remaining, so no single exchange can
    step the score discontinuously.

    Results are memoized in `_EVAL_CACHE` by Zobrist key: the search visits
    the same position many times over, and the static score of a position
    never changes, so the full scan below runs at most once per position.

    Parameters
    ----------
    gs : GameState
        The game state to evaluate. Checkmate/stalemate are handled by the
        search (which sees the empty move list), not here.

    Returns
    -------
    int
        Positive scores favor White, negative favor Black (centipawns).
    """
    # A position's static score is immutable, so the Zobrist key can answer
    # for it forever. Hits skip the entire scan below.
    key = gs.zobrist_key
    cached = _EVAL_CACHE.get(key)
    if cached is not None:
        return cached

    # Dead-drawn material scores exactly zero. The piece-count guard keeps
    # this from costing anything in normal positions.
    if len(gs.white_pieces) + len(gs.black_pieces) <= 4 and _insufficient_material(gs):
        if len(_EVAL_CACHE) >= _EVAL_CACHE_MAX:
            _EVAL_CACHE.clear()
        _EVAL_CACHE[key] = DRAW_SCORE
        return DRAW_SCORE

    board = gs.board
    score = 0
    non_pawn_material = 0
    white_bishops = 0
    black_bishops = 0
    white_pawns: list[tuple[int, int]] = []
    black_pawns: list[tuple[int, int]] = []
    # Per-file extremes of each side's pawns, used for the passed-pawn test.
    # Indexed by col + 1 with sentinel columns on both edges so a pawn's
    # neighbor files never need bounds checks. A white pawn is passed when no
    # black pawn sits ahead of it (lower row) on its own or adjacent files —
    # i.e. the black minimum row on those files is not below the pawn's row.
    white_max_row = [-1] * 10
    black_min_row = [8] * 10
    # Pawns per file (same col + 1 sentinel indexing) drive the doubled/
    # isolated penalties and the rook file bonuses below.
    white_file_pawns = [0] * 10
    black_file_pawns = [0] * 10
    white_rooks: list[tuple[int, int]] = []
    black_rooks: list[tuple[int, int]] = []

    for row, col in gs.white_pieces:
        piece = board[row][col]
        piece_type = PIECE_TYPE[piece]
        if piece_type == KING:
            continue
        score += PIECE_VALUES[piece] + PST[piece_type][row][col]
        if piece_type == PAWN:
            white_pawns.append((row, col))
            white_file_pawns[col + 1] += 1
            if row > white_max_row[col + 1]:
                white_max_row[col + 1] = row
        else:
            non_pawn_material += PIECE_VALUES[piece]
            score += MOBILITY_BONUS[piece_type] * _mobility(board, row, col, piece_type, True)
            if piece_type == BISHOP:
                white_bishops += 1
            elif piece_type == ROOK:
                white_rooks.append((row, col))

    for row, col in gs.black_pieces:
        piece = board[row][col]
        piece_type = PIECE_TYPE[piece]
        if piece_type == KING:
            continue
        # Mirror the table vertically for Black
        score -= PIECE_VALUES[piece] + PST[piece_type][7 - row][col]
        if piece_type == PAWN:
            black_pawns.append((row, col))
            black_file_pawns[col + 1] += 1
            if row < black_min_row[col + 1]:
                black_min_row[col + 1] = row
        else:
            non_pawn_material += PIECE_VALUES[piece]
            score -= MOBILITY_BONUS[piece_type] * _mobility(board, row, col, piece_type, False)
            if piece_type == BISHOP:
                black_bishops += 1
            elif piece_type == ROOK:
                black_rooks.append((row, col))

    if white_bishops >= 2:
        score += BISHOP_PAIR_BONUS
    if black_bishops >= 2:
        score -= BISHOP_PAIR_BONUS

    # Doubled/isolated pawns, judged per file. The sentinel columns mean the
    # adjacent-file lookups never need bounds checks, same as the passed-pawn
    # test below.
    for file_index in range(1, 9):
        count = white_file_pawns[file_index]
        if count:
            if count > 1:
                score -= DOUBLED_PAWN_PENALTY * (count - 1)
            if not white_file_pawns[file_index - 1] and not white_file_pawns[file_index + 1]:
                score -= ISOLATED_PAWN_PENALTY * count
        count = black_file_pawns[file_index]
        if count:
            if count > 1:
                score += DOUBLED_PAWN_PENALTY * (count - 1)
            if not black_file_pawns[file_index - 1] and not black_file_pawns[file_index + 1]:
                score += ISOLATED_PAWN_PENALTY * count

    # Rook activity: open/semi-open files and the 7th rank.
    for row, col in white_rooks:
        if not white_file_pawns[col + 1]:
            score += (ROOK_SEMI_OPEN_FILE_BONUS if black_file_pawns[col + 1]
                      else ROOK_OPEN_FILE_BONUS)
        if row == 1:
            score += ROOK_ON_SEVENTH_BONUS
    for row, col in black_rooks:
        if not black_file_pawns[col + 1]:
            score -= (ROOK_SEMI_OPEN_FILE_BONUS if white_file_pawns[col + 1]
                      else ROOK_OPEN_FILE_BONUS)
        if row == 6:
            score -= ROOK_ON_SEVENTH_BONUS

    # Game phase for the tapered terms below: 256 with all non-pawn material
    # on the board, 0 when only kings and pawns remain, linear in between.
    phase = min(PHASE_MAX, non_pawn_material * PHASE_MAX // PHASE_MATERIAL_MAX)
    end_phase = PHASE_MAX - phase

    for row, col in white_pawns:
        if (black_min_row[col] >= row
                and black_min_row[col + 1] >= row
                and black_min_row[col + 2] >= row):
            score += (PASSED_PAWN_BONUS[row] * phase
                      + PASSED_PAWN_BONUS_END[row] * end_phase) // PHASE_MAX

    for row, col in black_pawns:
        if (white_max_row[col] <= row
                and white_max_row[col + 1] <= row
                and white_max_row[col + 2] <= row):
            score -= (PASSED_PAWN_BONUS[7 - row] * phase
                      + PASSED_PAWN_BONUS_END[7 - row] * end_phase) // PHASE_MAX

    # The kings' tables blend the same way: the safety table dominates while
    # attackers remain, the centralization table takes over as they vanish.
    wk_row, wk_col = gs.white_king_location
    bk_row, bk_col = gs.black_king_location
    score += (KING_MID_PST[wk_row][wk_col] * phase
              + KING_END_PST[wk_row][wk_col] * end_phase) // PHASE_MAX
    score -= (KING_MID_PST[7 - bk_row][bk_col] * phase
              + KING_END_PST[7 - bk_row][bk_col] * end_phase) // PHASE_MAX

    # Pawn shield: scaled by phase rather than switched off at a threshold —
    # shelter matters exactly as much as there are attackers left to fear.
    if phase:
        shield = (KING_SHIELD_BONUS * _pawn_shield(board, wk_row, wk_col, WP, -1)
                  - KING_SHIELD_BONUS * _pawn_shield(board, bk_row, bk_col, BP, 1))
        score += shield * phase // PHASE_MAX

    if len(_EVAL_CACHE) >= _EVAL_CACHE_MAX:
        _EVAL_CACHE.clear()
    _EVAL_CACHE[key] = score
    return score


def _mobility(
    board: list[list[int]], row: int, col: int, piece_type: int, white: bool
) -> int:
    """
    Count the squares a knight or sliding piece can move to or capture on.

    A cheap pseudo-legal count: pins and checks are ignored (the search
    resolves those), and a square counts if it is empty or holds an enemy
    piece. Sliders stop at the first blocker, counting it when capturable.

    Parameters
    ----------
    board : list of list of int
        The board grid of integer piece codes.
    row, col : int
        The piece's square.
    piece_type : int
        One of KNIGHT, BISHOP, ROOK, QUEEN (pawns and kings score no
        mobility, so they are never passed here).
    white : bool
        The piece's colour, deciding which occupants count as capturable.

    Returns
    -------
    int
        The number of reachable squares.
    """
    count = 0
    if piece_type == KNIGHT:
        for r, c in _KNIGHT_TARGETS[row][col]:
            piece = board[r][c]
            # `0 < piece < 7` is the standard "is white" test; a square
            # counts when empty or when its occupant is the enemy's.
            if piece == EMPTY or (0 < piece < 7) != white:
                count += 1
    else:
        # Rays are precomputed and already on-board, so the walk needs no
        # bounds test — it just stops at the first blocker.
        for ray in _MOBILITY_RAYS[piece_type][row][col]:
            for r, c in ray:
                piece = board[r][c]
                if piece == EMPTY:
                    count += 1
                    continue
                if (0 < piece < 7) != white:
                    count += 1  # the blocker is capturable
                break
    return count


def _pawn_shield(
    board: list[list[int]], king_row: int, king_col: int, pawn: int, forward: int
) -> int:
    """
    Count friendly pawns sheltering a castled (or home-rank) king.

    Looks at the three files around the king and the two ranks directly in
    front of it, counting at most one pawn per file — a pawn one step ahead
    shields no better *and* no worse for our purposes than one two steps
    ahead, but a doubled pawn must not count twice.

    Parameters
    ----------
    board : list of list of str
        The board grid.
    king_row, king_col : int
        The king's square.
    pawn : int
        The friendly pawn code (WP or BP).
    forward : int
        The direction the shield extends: -1 for White (toward row 0),
        +1 for Black.

    Returns
    -------
    int
        Number of shielding pawns (0-3). Always 0 for a king that has left
        its back two ranks — it has no shelter left to score.
    """
    if forward == -1 and king_row < 6:
        return 0
    if forward == 1 and king_row > 1:
        return 0

    count = 0
    for col in (king_col - 1, king_col, king_col + 1):
        if 0 <= col < 8:
            for step in (1, 2):
                row = king_row + forward * step
                if 0 <= row < 8 and board[row][col] == pawn:
                    count += 1
                    break  # one pawn per file
    return count


def _insufficient_material(gs: GameState) -> bool:
    """
    Detect positions where neither side can possibly deliver mate.

    Covers the classic dead draws: K vs K, king + single minor vs king
    (or vs king + single minor), and KNN vs K — two knights cannot force
    mate. Any pawn, rook, or queen on the board means mate remains possible,
    as does a bishop pair or bishop + knight (both are forced mates).

    Parameters
    ----------
    gs : GameState
        The game state to inspect (callers should pre-filter on piece count;
        with more than four pieces on the board this can never be true).

    Returns
    -------
    bool
        True when the material is a dead draw.
    """
    board = gs.board
    white_minors: list[int] = []
    black_minors: list[int] = []

    for pieces, minors in ((gs.white_pieces, white_minors), (gs.black_pieces, black_minors)):
        for row, col in pieces:
            piece_type = PIECE_TYPE[board[row][col]]
            if piece_type == KING:
                continue
            if piece_type in (BISHOP, KNIGHT):
                minors.append(piece_type)
            else:
                return False  # a pawn, rook, or queen can still deliver mate

    if len(white_minors) <= 1 and len(black_minors) <= 1:
        return True
    # Two knights (and nothing else) cannot force mate against a bare king
    return ((white_minors == [KNIGHT, KNIGHT] and not black_minors)
            or (black_minors == [KNIGHT, KNIGHT] and not white_minors))
