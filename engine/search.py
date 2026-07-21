"""
The search: iterative-deepening negamax with alpha-beta, and the clock that
decides when to stop.

Entry points are `find_best_move()` (returns a move) and `search_position()`
(returns a move and its score, which is what the analysis screen needs).
Everything below them operates on the lightweight 5-tuple move format, never
on `Move` objects — the conversion happens at the boundary, in the caller.

The machinery, roughly in the order a node meets it: transposition probe,
null-move pruning, reverse-futility and futility pruning, late-move
reductions, check extension, then quiescence at the leaves. Move ordering is
MVV-LVA plus killers and history, with static exchange evaluation deciding
whether a capture is worth searching at all.

Two draw rules live here rather than in the evaluation, both for the same
reason: they depend on the *history* of the game, not just the position on the
board, and `eval._EVAL_CACHE` is keyed on the Zobrist key alone. Repetition is
tracked through `SearchInfo.rep_counts`; the 50-move clock through
`_fade_toward_draw()` and a hard cut at `HALFMOVE_DRAW_LIMIT`.

`_root_rng` shuffles equal-scored root moves so the engine does not play the
same game twice. It is seeded by `engine.tools.bench` deliberately: the
shuffle changes how much the search prunes, so node counts only reproduce when
it is pinned.
"""
import math
import random
import time
from typing import Callable

from engine.board import (
    AI_PROMO_TYPE, BISHOP, BN, BP, EMPTY, GameState, KING, PAWN, PIECE_TYPE,
    QUEEN, ROOK, WB, WN, WP, WQ, WR,
)
from engine.eval import (
    CHECKMATE_SCORE, DRAW_SCORE, MATE_THRESHOLD, PIECE_VALUES,
    _has_major_material, _rays_from, evaluate,
)
from engine.movegen import MoveTuple, generate_captures, generate_legal
from engine.tt import TT_EXACT, TT_LOWER, TT_UPPER, TTable

# --- The 50-move rule, as the search sees it ----------------------------
# 100 half-moves without a pawn move or capture is a draw. `make_ai_move`
# now maintains `halfmove_clock`, so the search can finally see it.
#
# A hard cut at 100 alone is not enough, and the reason is worth stating: at
# depth 6 the search would not notice the rule until the clock was already at
# 94, far too late to do anything about it. So the static evaluation is also
# *faded toward a draw* as the clock climbs, which gives the search a gradient
# it can act on many moves earlier — a +300 position that is 80 half-moves
# stale scores well under +300, and a progress move that resets the clock
# becomes visibly better than another shuffle.
#
# The fade deliberately starts late. Scaling from move one would distort
# ordinary middlegame positions, where a 30-move maneuvering phase is normal
# play and not a failure to progress.
HALFMOVE_DRAW_LIMIT = 100
HALFMOVE_FADE_START = 40  # below this the clock has no effect at all

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
# on it were measured and dropped. Scored (by the since-retired `tm_replay`)
# against the
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
# score-neutral (see `engine.tools.bench`) — a self-play Elo match cannot resolve
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
        valid_moves = generate_legal(gs, for_ai=True)
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


def _fade_toward_draw(score: int, halfmove_clock: int) -> int:
    """
    Shrink a static score toward a draw as the 50-move clock runs down.

    A won position that has made no progress for 80 half-moves is worth much
    less than the same position at move 5, because the win is about to be
    taken away by rule. Fading the score is what turns that fact into
    something the search can act on: a move that resets the clock keeps its
    full value while another shuffle does not, so progress starts winning the
    comparison well before the hard limit arrives.

    Mate scores are returned untouched. A forced mate is a forced mate — if
    the search has proved one, the 50-move clock is irrelevant to it, and
    scaling it would corrupt the mate-distance ordering.

    Parameters
    ----------
    score : int
        Static evaluation from the side to move's perspective.
    halfmove_clock : int
        Half-moves since the last pawn move or capture.

    Returns
    -------
    int
        The score, scaled linearly to zero between `HALFMOVE_FADE_START` and
        `HALFMOVE_DRAW_LIMIT`.
    """
    if halfmove_clock <= HALFMOVE_FADE_START:
        return score
    if score >= MATE_THRESHOLD or score <= -MATE_THRESHOLD:
        return score
    # Clamped at zero: quiescence has no 50-move guard of its own, so it can
    # be handed a clock past the limit, and an unclamped negative multiplier
    # would flip the sign of the score rather than flatten it.
    remaining = max(0, HALFMOVE_DRAW_LIMIT - halfmove_clock)
    return score * remaining // (HALFMOVE_DRAW_LIMIT - HALFMOVE_FADE_START)


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

    # ...and so does running out the 50-move clock. Checked here rather than
    # inside `evaluate()` because `_EVAL_CACHE` is keyed on the Zobrist key
    # alone, and `halfmove_clock` is not part of that key — a position's
    # cached static score must stay a pure function of the position.
    if gs.halfmove_clock >= HALFMOVE_DRAW_LIMIT:
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

    moves = generate_legal(gs, for_ai=True)
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
        static_eval = _fade_toward_draw(
            evaluate(gs) if gs.white_to_move else -evaluate(gs),
            gs.halfmove_clock)

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
    stand_pat = _fade_toward_draw(turn * evaluate(gs), gs.halfmove_clock)

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
    noisy = generate_legal(gs, for_ai=True, captures_only=True)
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
