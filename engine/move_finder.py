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
- Static evaluation combining material and piece-square tables

The search mutates the GameState in place via `make_ai_move()` /
`unmake_ai_move()` and always restores it before returning, so the caller's
state is untouched. When running the search on a background thread, pass a
`copy.deepcopy` of the GameState so the UI can keep rendering the original.
"""
import random
import time

from engine.chess_engine import GameState

# Type alias for the lightweight move format shared with chess_engine
MoveTuple = tuple[int, int, int, int, int]

# --- Evaluation constants ---
PIECE_VALUES: dict[str, int] = {'K': 0, 'Q': 900, 'R': 500, 'B': 330, 'N': 320, 'P': 100}

CHECKMATE_SCORE = 100_000
MATE_THRESHOLD = 90_000  # Scores beyond this are "mate in N" scores
DRAW_SCORE = 0

# Piece-square tables (white's perspective, row 0 = rank 8).
# Values follow Tomasz Michniewski's "Simplified Evaluation Function".
# Black uses the same tables mirrored vertically (row -> 7 - row).
PST: dict[str, tuple[tuple[int, ...], ...]] = {
    'P': (
        (0, 0, 0, 0, 0, 0, 0, 0),
        (50, 50, 50, 50, 50, 50, 50, 50),
        (10, 10, 20, 30, 30, 20, 10, 10),
        (5, 5, 10, 25, 25, 10, 5, 5),
        (0, 0, 0, 20, 20, 0, 0, 0),
        (5, -5, -10, 0, 0, -10, -5, 5),
        (5, 10, 10, -20, -20, 10, 10, 5),
        (0, 0, 0, 0, 0, 0, 0, 0),
    ),
    'N': (
        (-50, -40, -30, -30, -30, -30, -40, -50),
        (-40, -20, 0, 0, 0, 0, -20, -40),
        (-30, 0, 10, 15, 15, 10, 0, -30),
        (-30, 5, 15, 20, 20, 15, 5, -30),
        (-30, 0, 15, 20, 20, 15, 0, -30),
        (-30, 5, 10, 15, 15, 10, 5, -30),
        (-40, -20, 0, 5, 5, 0, -20, -40),
        (-50, -40, -30, -30, -30, -30, -40, -50),
    ),
    'B': (
        (-20, -10, -10, -10, -10, -10, -10, -20),
        (-10, 0, 0, 0, 0, 0, 0, -10),
        (-10, 0, 5, 10, 10, 5, 0, -10),
        (-10, 5, 5, 10, 10, 5, 5, -10),
        (-10, 0, 10, 10, 10, 10, 0, -10),
        (-10, 10, 10, 10, 10, 10, 10, -10),
        (-10, 5, 0, 0, 0, 0, 5, -10),
        (-20, -10, -10, -10, -10, -10, -10, -20),
    ),
    'R': (
        (0, 0, 0, 0, 0, 0, 0, 0),
        (5, 10, 10, 10, 10, 10, 10, 5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (-5, 0, 0, 0, 0, 0, 0, -5),
        (0, 0, 0, 5, 5, 0, 0, 0),
    ),
    'Q': (
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

# Below this much non-pawn material on the board, king tables switch to endgame
ENDGAME_MATERIAL_THRESHOLD = 2600

# Transposition table bound flags
TT_EXACT, TT_LOWER, TT_UPPER = 0, 1, 2

# Null-move pruning depth reduction
NULL_MOVE_REDUCTION = 2

_root_rng = random.Random()


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
    tt : dict of int to tuple
        Transposition table: zobrist_key -> (depth, flag, score, best_move).
    killers : list of list of MoveTuple
        Two killer moves (quiet beta-cutoff moves) per ply.
    history : dict of tuple to int
        History heuristic scores for quiet move ordering.
    rep_counts : dict of int, int
        Occurrence counts of Zobrist keys along game history + search path.
    """

    def __init__(self, time_limit: float, rep_counts: dict[int, int]) -> None:
        self.start_time: float = time.perf_counter()
        self.time_limit: float = time_limit
        self.nodes: int = 0
        self.tt: dict[int, tuple[int, int, int, MoveTuple | None]] = {}
        self.killers: list[list[MoveTuple]] = [[] for _ in range(64)]
        self.history: dict[tuple[int, int, int, int], int] = {}
        self.rep_counts: dict[int, int] = rep_counts

    def check_time(self) -> None:
        """Raise SearchTimeout if the soft time limit has expired."""
        # Only sample the clock every 2048 nodes: perf_counter is not free
        if self.nodes % 2048 == 0:
            if time.perf_counter() - self.start_time > self.time_limit:
                raise SearchTimeout


def find_best_move(
    gs: GameState,
    valid_moves: list[MoveTuple] | None = None,
    max_depth: int = 4,
    time_limit: float = 5.0,
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

        AI_PLANNING: this parameter is the clock-management hook for Lichess
        play — uci.py derives it from the server's wtime/btime/winc fields,
        and iterative deepening guarantees a legal answer whenever it expires.

    Returns
    -------
    MoveTuple or None
        The best move tuple found, or None if the position has no legal moves.
    """
    if valid_moves is None:
        valid_moves = gs.get_valid_moves(for_ai=True)
    if not valid_moves:
        return None

    # Seed repetition detection with the real game history so the engine
    # recognizes (and can aim for or avoid) threefold repetitions
    rep_counts: dict[int, int] = {}
    for key in getattr(gs, 'zobrist_history', [gs.zobrist_key]):
        rep_counts[key] = rep_counts.get(key, 0) + 1

    info = SearchInfo(time_limit, rep_counts)

    # Shuffle once so equal-scoring moves vary between games
    root_moves = list(valid_moves)
    _root_rng.shuffle(root_moves)

    best_move: MoveTuple | None = root_moves[0]
    best_score = -CHECKMATE_SCORE

    for depth in range(1, max_depth + 1):
        try:
            score, move = _search_root(gs, root_moves, depth, info)
        except SearchTimeout:
            break  # Keep the result of the last completed iteration

        if move is not None:
            best_move, best_score = move, score
            # Re-order the root list so the current best is searched first
            root_moves.remove(move)
            root_moves.insert(0, move)

        # A forced mate found: deeper search cannot improve it
        if abs(best_score) >= MATE_THRESHOLD:
            break

    return best_move


def _search_root(
    gs: GameState,
    root_moves: list[MoveTuple],
    depth: int,
    info: SearchInfo,
) -> tuple[int, MoveTuple | None]:
    """
    Search all root moves at a fixed depth and return (score, best_move).

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

    Returns
    -------
    tuple
        (best_score, best_move) from the side to move's perspective.
    """
    alpha, beta = -CHECKMATE_SCORE, CHECKMATE_SCORE
    best_move: MoveTuple | None = None

    for move in root_moves:
        undo = gs.make_ai_move(move)
        child_key = gs.zobrist_key
        info.rep_counts[child_key] = info.rep_counts.get(child_key, 0) + 1
        try:
            score = -_negamax(gs, depth - 1, -beta, -alpha, 1, info)
        finally:
            info.rep_counts[child_key] -= 1
            gs.unmake_ai_move(move, undo)

        if score > alpha:
            alpha = score
            best_move = move

    return alpha, best_move


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
        tt_depth, tt_flag, tt_score, tt_move = entry
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

    original_alpha = alpha
    best_score = -CHECKMATE_SCORE
    best_move: MoveTuple | None = None

    for move in ordered:
        undo = gs.make_ai_move(move)
        child_key = gs.zobrist_key
        info.rep_counts[child_key] = info.rep_counts.get(child_key, 0) + 1
        try:
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
            if undo[0] == '--' and move[4] < 3:
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
        info.tt[key] = (depth, flag, best_score, best_move)

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

    turn = 1 if gs.white_to_move else -1
    stand_pat = turn * evaluate(gs)

    if stand_pat >= beta:
        return beta
    if stand_pat > alpha:
        alpha = stand_pat

    moves = gs.get_valid_moves(for_ai=True)
    if not moves:
        return -(CHECKMATE_SCORE - ply) if gs.in_check else DRAW_SCORE

    board = gs.board
    noisy = [
        m for m in moves
        if board[m[2]][m[3]] != '--' or m[4] == 2 or m[4] >= 3
    ]
    noisy.sort(key=lambda m: _mvv_lva(gs, m), reverse=True)

    for move in noisy:
        undo = gs.make_ai_move(move)
        try:
            score = -_quiescence(gs, -beta, -alpha, ply + 1, info)
        finally:
            gs.unmake_ai_move(move, undo)

        if score >= beta:
            return beta
        if score > alpha:
            alpha = score

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
        if victim != '--' or move[4] == 2:
            return 1_000_000 + _mvv_lva(gs, move)
        if move[4] >= 3:  # Quiet promotions
            return 900_000 + PIECE_VALUES[_promo_piece(move[4])]
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
    victim_value = PIECE_VALUES['P'] if move[4] == 2 else (
        PIECE_VALUES[victim[1]] if victim != '--' else 0
    )
    attacker_value = PIECE_VALUES[board[move[0]][move[1]][1]]
    promo_bonus = PIECE_VALUES[_promo_piece(move[4])] if move[4] >= 3 else 0
    return victim_value * 10 - attacker_value + promo_bonus


def _promo_piece(move_type: int) -> str:
    """Map an AI promotion move-type code (3-6) to its piece letter."""
    return {3: 'Q', 4: 'R', 5: 'B', 6: 'N'}[move_type]


def _has_major_material(gs: GameState) -> bool:
    """
    Check whether the side to move still owns a non-pawn, non-king piece.

    Null-move pruning is unsound in pawn-only endings (zugzwang positions),
    so the search disables it when this returns False.
    """
    board = gs.board
    pieces = gs.white_pieces if gs.white_to_move else gs.black_pieces
    for row, col in pieces:
        if board[row][col][1] not in ('P', 'K'):
            return True
    return False


def evaluate(gs: GameState) -> int:
    """
    Static evaluation of the position from White's perspective.

    Combines raw material with piece-square table bonuses. Kings switch from
    a safety-oriented table to a centralization table once the total non-pawn
    material drops below the endgame threshold.

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
    board = gs.board
    score = 0
    non_pawn_material = 0

    for row, col in gs.white_pieces:
        piece_type = board[row][col][1]
        if piece_type == 'K':
            continue
        score += PIECE_VALUES[piece_type] + PST[piece_type][row][col]
        if piece_type != 'P':
            non_pawn_material += PIECE_VALUES[piece_type]

    for row, col in gs.black_pieces:
        piece_type = board[row][col][1]
        if piece_type == 'K':
            continue
        # Mirror the table vertically for Black
        score -= PIECE_VALUES[piece_type] + PST[piece_type][7 - row][col]
        if piece_type != 'P':
            non_pawn_material += PIECE_VALUES[piece_type]

    king_table = KING_END_PST if non_pawn_material <= ENDGAME_MATERIAL_THRESHOLD else KING_MID_PST
    wk_row, wk_col = gs.white_king_location
    bk_row, bk_col = gs.black_king_location
    score += king_table[wk_row][wk_col]
    score -= king_table[7 - bk_row][bk_col]

    return score


def score_board(gs: GameState) -> float:
    """
    Static evaluation wrapper kept for backward compatibility.

    Positive score favors White, negative favors Black. Prefer `evaluate()`
    in new code; this wrapper additionally maps the engine's terminal flags
    to mate/draw scores the way the old minimax implementation expected.

    Returns
    -------
    float
        The calculated numerical evaluation of the board.
    """
    if gs.is_checkmate:
        return -CHECKMATE_SCORE if gs.white_to_move else CHECKMATE_SCORE
    if gs.is_stalemate:
        return DRAW_SCORE
    return float(evaluate(gs))
