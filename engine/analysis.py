"""
Move-quality analysis for the game-review feature (chess.com style).

The core idea mirrors chess.com's Game Review: a raw centipawn score is hard
to reason about, so every evaluation is first converted into a *win
percentage* — the mover's expected chance of winning — using the logistic
curve popularized by Lichess's accuracy metric. A move is then graded by how
much win percentage it threw away compared to the engine's preferred move:

- ``best`` (played the engine move), ``excellent``, ``good``, ``inaccuracy``,
  ``mistake``, ``blunder``: a ladder of increasing win% loss, with thresholds
  close to chess.com's published bands (~1 / 3.5 / 7 / 10 / 20 percent).
- ``brilliant``: a best move that *sacrifices* material — chess.com only
  awards it when you weren't losing after the move and weren't already
  completely winning without it.
- ``great_find``: the *only* good move — every alternative would have
  significantly worsened the position (verified with a second search that
  excludes the best move).
- ``missed_win``: the mover had a forced mate or an overwhelming position and
  let it slip ("Miss" on chess.com).
- ``book``: still inside well-known opening theory.
- ``forced``: the only legal move, so no credit and no blame.

Analysing a whole game just means evaluating every position once: the score
of position *i+1* serves both as "what the played move achieved" for move
*i* and as the baseline for move *i+1*, so a game of N moves costs N+1
searches (plus an occasional second search for great-move detection).

`GameAnalysis` packages that loop on a daemon thread so the GUI can render
progressively, but the module stays pure stdlib like the rest of `engine/`.
"""
import math
import threading
import time
from dataclasses import dataclass

from engine import pgn
from engine.chess_engine import (
    ALL_DIRECTIONS,
    KNIGHT_DELTAS,
    GameState,
    PIECE_TYPE,
    EMPTY, PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING,
    WN, BN, BP,
)
from engine.move_finder import (
    CHECKMATE_SCORE,
    MATE_THRESHOLD,
    PIECE_VALUES,
    search_position,
)

# Type alias matching move_finder's lightweight move format
MoveTuple = tuple[int, int, int, int, int]

# Move-quality tags. The values double as the evaluate_icons/ file stems the
# GUI uses to pick each tag's badge image.
BRILLIANT = 'brilliant'
GREAT = 'great_find'
BEST = 'best'
EXCELLENT = 'excellent'
GOOD = 'good'
BOOK = 'book'
INACCURACY = 'inaccuracy'
MISTAKE = 'mistake'
MISS = 'missed_win'
BLUNDER = 'blunder'
FORCED = 'forced'

# Logistic steepness of the centipawns -> win% curve (the constant Lichess
# fitted for its accuracy metric; chess.com's model behaves very similarly)
_WIN_PERCENT_K = 0.00368208

# Win%-loss ladder, patterned after chess.com's published thresholds
_BEST_DROP = 1.0
_EXCELLENT_DROP = 3.5
_GOOD_DROP = 7.0
_INACCURACY_DROP = 10.0
_MISTAKE_DROP = 20.0

# A "great find" must beat the second-best move by this much win% and the
# alternative must not itself still be fine
_GREAT_GAP = 15.0
_GREAT_SECOND_BEST_CAP = 60.0

# Brilliant guards: not losing after the sacrifice, not already crushing
_BRILLIANT_MIN_AFTER = 40.0
_BRILLIANT_MAX_BEFORE = 95.0

# A "miss" is only called when real damage was done
_MISS_MIN_DROP = 4.0

# Material a move must give up (beyond what it captures) to read as a
# sacrifice, in centipawns — roughly "a minor piece for a pawn or worse"
_SACRIFICE_MARGIN = 200


@dataclass
class PositionEval:
    """
    The engine's verdict on one position of the reviewed game.

    Attributes
    ----------
    score_white : int
        Evaluation in centipawns from White's perspective. Mate scores are
        encoded the way the search encodes them (±(CHECKMATE_SCORE - plies)).
    best_move : MoveTuple or None
        The engine's preferred move, or None at a terminal position.
    legal_moves : int
        Number of legal moves in the position (1 means the reply is forced).
    """
    score_white: int
    best_move: MoveTuple | None
    legal_moves: int


def win_percent(score_cp: int) -> float:
    """
    Convert a centipawn score into an expected win percentage (0-100).

    Parameters
    ----------
    score_cp : int
        Score in centipawns from the perspective of the player whose winning
        chances are being asked about. Mate scores map to 100 or 0.

    Returns
    -------
    float
        The expected win percentage: 50.0 means dead equal.
    """
    if score_cp >= MATE_THRESHOLD:
        return 100.0
    if score_cp <= -MATE_THRESHOLD:
        return 0.0
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-_WIN_PERCENT_K * score_cp)) - 1.0)


def mate_in(score_cp: int) -> int | None:
    """
    Extract the moves-to-mate from a mate-encoded score.

    Parameters
    ----------
    score_cp : int
        A search score; mate scores hold CHECKMATE_SCORE minus the mating
        line's length in plies.

    Returns
    -------
    int or None
        Full moves until mate (0 for "already checkmated"), or None for a
        normal, non-mate score. The sign is dropped — combine with the score's
        own sign to know who is mating.
    """
    if abs(score_cp) < MATE_THRESHOLD:
        return None
    plies = CHECKMATE_SCORE - abs(score_cp)
    return (plies + 1) // 2


def format_eval(score_white: int) -> str:
    """
    Render a White-perspective score the way an eval bar labels it.

    Parameters
    ----------
    score_white : int
        Evaluation in centipawns from White's perspective.

    Returns
    -------
    str
        '+1.3' / '-0.4' for normal scores, 'M5' / '-M3' for forced mates,
        and '#' when the position is already checkmate.
    """
    mate = mate_in(score_white)
    if mate is not None:
        if mate == 0:
            return '#'
        return f'M{mate}' if score_white > 0 else f'-M{mate}'
    return f'{score_white / 100:+.1f}'


def evaluate_position(
    gs: GameState,
    max_depth: int,
    time_limit: float,
    exclude: MoveTuple | None = None,
) -> PositionEval:
    """
    Search one position and return a White-perspective assessment.

    Parameters
    ----------
    gs : GameState
        The position to evaluate (mutated during search, then restored).
    max_depth : int
        Iterative-deepening depth cap for the search.
    time_limit : float
        Soft time limit in seconds for the search.
    exclude : MoveTuple, optional
        A move to remove from the root move list; used to score the best
        *alternative* when checking whether a move was the only good one.

    Returns
    -------
    PositionEval
        Score (White's perspective), best move, and legal move count.
    """
    moves = gs.get_valid_moves(for_ai=True)
    legal_count = len(moves)
    if exclude is not None:
        moves = [m for m in moves if m != exclude]

    best_move, score = search_position(gs, moves, max_depth, time_limit)
    # The search reports from the side to move's perspective; the review
    # works in White-perspective scores so the eval bar never flips sign
    score_white = score if gs.white_to_move else -score
    return PositionEval(score_white, best_move, legal_count)


def classify_move(
    move: MoveTuple,
    before: PositionEval,
    after: PositionEval,
    white_moved: bool,
    in_book: bool,
    sacrifice: bool,
    second_best_score_white: int | None = None,
) -> str:
    """
    Grade one played move, chess.com Game Review style.

    Parameters
    ----------
    move : MoveTuple
        The move that was played.
    before : PositionEval
        Evaluation of the position the move was played in.
    after : PositionEval
        Evaluation of the position the move produced.
    white_moved : bool
        True when White played the move (selects the win% perspective).
    in_book : bool
        True when the resulting position is still known opening theory.
    sacrifice : bool
        True when the move deliberately gives up material (see
        `is_sacrifice`).
    second_best_score_white : int, optional
        White-perspective score of the position searched *without* the best
        move — supplied only when the caller ran the extra uniqueness search
        (see `needs_uniqueness_check`).

    Returns
    -------
    str
        One of the module's tag constants (BRILLIANT ... FORCED).
    """
    if in_book:
        return BOOK
    if before.legal_moves == 1:
        return FORCED

    sign = 1 if white_moved else -1
    wp_before = win_percent(sign * before.score_white)
    wp_after = win_percent(sign * after.score_white)
    drop = wp_before - wp_after

    # Top of the ladder: the engine move itself, or one just as strong
    if move == before.best_move or drop <= _BEST_DROP:
        if sacrifice and wp_after >= _BRILLIANT_MIN_AFTER and wp_before <= _BRILLIANT_MAX_BEFORE:
            return BRILLIANT
        if second_best_score_white is not None:
            wp_second = win_percent(sign * second_best_score_white)
            if wp_after - wp_second >= _GREAT_GAP and wp_second <= _GREAT_SECOND_BEST_CAP:
                return GREAT
        return BEST

    # A "miss": a forced mate or an overwhelming position slipped away
    mate_before = sign * before.score_white >= MATE_THRESHOLD
    mate_after = sign * after.score_white >= MATE_THRESHOLD
    if drop > _MISS_MIN_DROP:
        if (mate_before and not mate_after) or (wp_before >= 90.0 and wp_after <= 65.0):
            return MISS

    if drop <= _EXCELLENT_DROP:
        return EXCELLENT
    if drop <= _GOOD_DROP:
        return GOOD
    if drop <= _INACCURACY_DROP:
        return INACCURACY
    if drop <= _MISTAKE_DROP:
        return MISTAKE
    return BLUNDER


def needs_uniqueness_check(
    move: MoveTuple,
    before: PositionEval,
    after: PositionEval,
    white_moved: bool,
    in_book: bool,
) -> bool:
    """
    Decide whether the extra "was this the only good move?" search is worth it.

    The second search is the expensive part of great-move detection, so it
    only runs when the played move was the engine's choice in a position that
    is neither trivial (forced/book) nor already decided.

    Parameters
    ----------
    move, before, after, white_moved, in_book
        Same meaning as in `classify_move`.

    Returns
    -------
    bool
        True when the caller should search the position again with the best
        move excluded and pass the result to `classify_move`.
    """
    if in_book or before.legal_moves <= 2 or move != before.best_move:
        return False
    sign = 1 if white_moved else -1
    if sign * before.score_white >= MATE_THRESHOLD:
        return False  # Mates are "best", not "great finds"
    wp_after = win_percent(sign * after.score_white)
    return 30.0 <= wp_after <= 90.0


def is_sacrifice(gs: GameState, move: MoveTuple) -> bool:
    """
    Heuristically decide whether a move deliberately gives up material.

    This is a deliberately lightweight stand-in for a full static exchange
    evaluation: the move must put a piece (not a pawn or the king) somewhere
    the opponent can profitably take it — either the square is undefended, or
    the cheapest capturer is worth less than what it wins. X-rays and pins
    are ignored, which is acceptable for a badge that only upgrades an
    already-best move to "brilliant".

    Parameters
    ----------
    gs : GameState
        The position *before* the move (side to move is the mover).
    move : MoveTuple
        The candidate sacrifice.

    Returns
    -------
    bool
        True when the move offers at least ~2 pawns of net material.
    """
    start_row, start_col, end_row, end_col, move_type = move
    board = gs.board
    piece = board[start_row][start_col]
    kind = PIECE_TYPE[piece]
    if kind in (PAWN, KING):
        return False

    moved_value = PIECE_VALUES[piece]
    target = board[end_row][end_col]
    captured_value = PIECE_VALUES[target] if target != EMPTY else 0
    if moved_value - captured_value < _SACRIFICE_MARGIN:
        return False  # Trading down this little is never a real sacrifice

    mover_color = 'w' if piece < BP else 'b'
    opponent_color = 'b' if mover_color == 'w' else 'w'

    # Look at the destination square with the move actually played, so the
    # moved piece no longer shields its own square
    undo = gs.make_ai_move(move)
    try:
        attackers = _attacker_values(board, end_row, end_col, opponent_color)
        defenders = _attacker_values(board, end_row, end_col, mover_color)
    finally:
        gs.unmake_ai_move(move, undo)

    if not attackers:
        return False
    if not defenders:
        return True  # Simply left hanging on purpose
    # Defended: only a cheaper attacker makes the capture profitable. A king
    # (value 0) cannot capture a defended square, so it is not an attacker
    # here.
    real_attackers = [value for value in attackers if value > 0]
    return bool(real_attackers) and real_attackers[0] < moved_value


def _attacker_values(
    board: list[list[int]],
    row: int,
    col: int,
    color: str,
) -> list[int]:
    """
    List the values of `color` pieces attacking a square, cheapest first.

    Scans outward from the target square — knight jumps, then each of the
    eight rays until a piece blocks it — which is much cheaper than asking
    every enemy piece whether it attacks the square.

    Parameters
    ----------
    board : list of list of str
        The board array.
    row, col : int
        The square being attacked.
    color : str
        'w' or 'b': whose attackers to count.

    Returns
    -------
    list of int
        PIECE_VALUES of each attacker, sorted ascending (kings score 0).
    """
    values: list[int] = []
    is_white = color == 'w'
    knight = WN if is_white else BN

    for d_row, d_col in KNIGHT_DELTAS:
        r, c = row + d_row, col + d_col
        if 0 <= r < 8 and 0 <= c < 8 and board[r][c] == knight:
            values.append(PIECE_VALUES[KNIGHT])

    for d_row, d_col in ALL_DIRECTIONS:
        diagonal = d_row != 0 and d_col != 0
        for dist in range(1, 8):
            r, c = row + d_row * dist, col + d_col * dist
            if not (0 <= r < 8 and 0 <= c < 8):
                break
            piece = board[r][c]
            if piece == EMPTY:
                continue
            if (piece < BP) == is_white:
                kind = PIECE_TYPE[piece]
                if kind == QUEEN or (kind == ROOK and not diagonal) or (kind == BISHOP and diagonal):
                    values.append(PIECE_VALUES[piece])
                elif dist == 1:
                    if kind == KING:
                        values.append(PIECE_VALUES[piece])
                    elif kind == PAWN and diagonal:
                        # A pawn attacks one row toward the enemy side, so a
                        # white pawn must sit one row *below* its target
                        if (is_white and d_row == 1) or (not is_white and d_row == -1):
                            values.append(PIECE_VALUES[piece])
            break  # Any piece, either color, blocks the rest of the ray
        # (knight deltas handled above; nothing more on this ray)

    return sorted(values)


def accuracy_from_drops(drops: list[float]) -> float:
    """
    Estimate a player's accuracy percentage from their per-move win% losses.

    Uses the exponential curve Lichess fitted for its accuracy metric (the
    same family of curve chess.com's accuracy score follows): zero average
    loss is ~100, and accuracy decays smoothly as the average loss grows.

    Parameters
    ----------
    drops : list of float
        Win% lost on each of the player's moves (0 for perfect moves).

    Returns
    -------
    float
        Accuracy percentage, clamped to 0-100.
    """
    if not drops:
        return 100.0
    average = sum(drops) / len(drops)
    accuracy = 103.1668 * math.exp(-0.04354 * average) - 3.1669
    return max(0.0, min(100.0, accuracy))


# ----------------------------------------------------------------------
# Opening book (for the "book" tag)
# ----------------------------------------------------------------------
# A miniature theory table: the first moves of the most common openings.
# Every position reached along one of these lines counts as "book". Lines
# are stored as SAN so they stay human-readable; they are replayed through
# the engine's own move generator the first time the book is needed.
_BOOK_LINES: tuple[str, ...] = (
    # 1. e4 e5
    'e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6',                    # Italian, Giuoco Piano
    'e4 e5 Nf3 Nc6 Bc4 Nf6 d3 Bc5',                          # Two Knights
    'e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6',    # Ruy Lopez, closed
    'e4 e5 Nf3 Nc6 Bb5 Nf6 O-O Nxe4 d4 Nd6 Bxc6 dxc6',       # Ruy Lopez, Berlin
    'e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6 Nxc6 bxc6 e5 Qe7',       # Scotch
    'e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5 Bd3',              # Petrov
    'e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4',                         # Vienna
    'e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4 O-O O-O d3 d6',           # Four Knights
    'e4 e5 Nf3 d6 d4 exd4 Nxd4 Nf6 Nc3 Be7',                 # Philidor
    'e4 e5 Nf3 Nc6 Bc4 Bc5 b4 Bxb4 c3 Ba5 d4 exd4 O-O',      # Evans Gambit
    'e4 e5 f4 exf4 Nf3 g5 h4 g4 Ne5',                        # King's Gambit
    # Sicilian
    'e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5',           # Najdorf
    'e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Be3 Bg7 f3 O-O',   # Dragon
    'e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 Nf6 Nc3 e5 Ndb5 d6',         # Sveshnikov
    'e4 c5 c3 Nf6 e5 Nd5 d4 cxd4 Nf3',                       # Alapin
    'e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7 d3 d6',                     # Closed Sicilian
    # Other 1. e4 defences
    'e4 e6 d4 d5 Nc3 Bb4 e5 c5 a3 Bxc3+ bxc3 Ne7',           # French, Winawer
    'e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3 Qb6',                      # French, advance
    'e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6 h4 h6 Nf3',       # Caro-Kann, classical
    'e4 c6 d4 d5 e5 Bf5 Nf3 e6 Be2 Nd7',                     # Caro-Kann, advance
    'e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 Bf5',                # Scandinavian
    'e4 d6 d4 Nf6 Nc3 g6 Be2 Bg7 Nf3 O-O O-O',               # Pirc
    # 1. d4
    'd4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6',             # Queen's Gambit Declined
    'd4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5 e3 e6',             # Slav
    'd4 d5 c4 c6 Nf3 Nf6 Nc3 e6 e3 Nbd7 Bd3 dxc4 Bxc4 b5',   # Semi-Slav
    'd4 d5 c4 dxc4 Nf3 Nf6 e3 e6 Bxc4 c5 O-O a6',            # Queen's Gambit Accepted
    'd4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5 O-O Nc6',     # King's Indian
    'd4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5 e4 Nxc3 bxc3 Bg7 Nf3 c5', # Grünfeld
    'd4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3 d5 Nf3 c5 O-O Nc6',     # Nimzo-Indian
    'd4 Nf6 c4 e6 Nf3 b6 g3 Bb7 Bg2 Be7 O-O O-O',            # Queen's Indian
    'd4 Nf6 c4 e6 g3 d5 Bg2 Be7 Nf3 O-O O-O dxc4',           # Catalan
    'd4 d5 Bf4 Nf6 e3 e6 Nf3 c5 c3 Nc6 Nbd2 Bd6 Bg3',        # London System
    # Flank openings
    'c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5 cxd5 Nxd5 Bg2 Nb6 O-O Be7', # English, reversed Sicilian
    'c4 c5 Nf3 Nf6 d4 cxd4 Nxd4 e6 g3',                      # English, symmetric
    'Nf3 d5 c4 e6 g3 Nf6 Bg2 Be7 O-O O-O',                   # Réti
)

# Lazily built: FEN position keys (first four fields) of every book position
_book_keys: set[str] | None = None
_book_lock = threading.Lock()


def _position_key(gs: GameState) -> str:
    """Identity of a position for book lookup: FEN without the move counters."""
    return ' '.join(gs.to_fen().split()[:4])


def _build_book() -> set[str]:
    """Replay every book line and collect the position keys along the way."""
    keys: set[str] = set()
    for line in _BOOK_LINES:
        gs = GameState()
        for token in line.split():
            move = pgn.san_to_move(gs, token)
            gs.make_move(move)
            keys.add(_position_key(gs))
    return keys


def is_book_position(gs: GameState) -> bool:
    """
    Check whether the current position is part of the built-in opening book.

    Parameters
    ----------
    gs : GameState
        The position *after* the move being judged.

    Returns
    -------
    bool
        True when the position lies on one of the known theory lines.
    """
    global _book_keys
    if _book_keys is None:
        with _book_lock:
            if _book_keys is None:  # Double-checked: build only once
                _book_keys = _build_book()
    return _position_key(gs) in _book_keys


# ----------------------------------------------------------------------
# Whole-game analysis worker
# ----------------------------------------------------------------------
class GameAnalysis:
    """
    Background analyser producing evaluations and tags for a whole game.

    A daemon thread replays the game move by move on its own GameState,
    evaluating each position once and grading each move as soon as the next
    position's evaluation exists. The GUI polls the public lists while the
    thread fills them in — entries are None until computed, and the lists
    only ever grow, so index-based reads from another thread are safe.

    Parameters
    ----------
    start_fen : str
        FEN of the game's first position.
    moves : list of MoveTuple
        The game's moves in the lightweight AI tuple format.
    max_depth : int
        Iterative-deepening depth cap per position.
    time_limit : float
        Soft time limit in seconds per position.

    Attributes
    ----------
    evals : list of PositionEval or None
        One entry per position; index i is the position before move i.
    tags : list of str or None
        One quality tag per move, filled in as analysis progresses.
    """

    def __init__(
        self,
        start_fen: str,
        moves: list[MoveTuple],
        max_depth: int,
        time_limit: float,
    ) -> None:
        self._start_fen = start_fen
        self._max_depth = max_depth
        self._time_limit = time_limit
        self._white_starts = start_fen.split()[1] == 'w'

        self._lock = threading.Lock()  # Guards _moves growth from the GUI
        self._moves: list[MoveTuple] = list(moves)
        self.evals: list[PositionEval | None] = [None] * (len(self._moves) + 1)
        self.tags: list[str | None] = [None] * len(self._moves)

        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def append_move(self, move: MoveTuple) -> None:
        """
        Extend the game with a move played on the analysis board.

        Parameters
        ----------
        move : MoveTuple
            The new move; it must be legal in the game's final position.
        """
        with self._lock:
            self._moves.append(move)
            self.evals.append(None)
            self.tags.append(None)

    def stop(self) -> None:
        """Ask the worker thread to exit after its current search."""
        self._stop = True

    @property
    def analysed_positions(self) -> int:
        """Number of positions whose evaluation is already available."""
        return sum(1 for entry in self.evals if entry is not None)

    @property
    def total_positions(self) -> int:
        """Total number of positions the game currently spans."""
        return len(self.evals)

    @property
    def done(self) -> bool:
        """True when every currently-known position has been analysed."""
        return self.analysed_positions == self.total_positions

    def accuracy(self, white: bool) -> float | None:
        """
        Compute one player's accuracy over the analysed moves.

        Parameters
        ----------
        white : bool
            True for White's accuracy, False for Black's.

        Returns
        -------
        float or None
            The accuracy percentage, or None until analysis has finished.
        """
        if not self.done:
            return None
        drops: list[float] = []
        for index in range(len(self.tags)):
            white_moved = self._white_starts == (index % 2 == 0)
            if white_moved != white:
                continue
            before, after = self.evals[index], self.evals[index + 1]
            if before is None or after is None:
                return None
            sign = 1 if white_moved else -1
            drop = win_percent(sign * before.score_white) - win_percent(sign * after.score_white)
            drops.append(max(0.0, drop))
        return accuracy_from_drops(drops)

    def _run(self) -> None:
        """Worker loop: evaluate each position, then grade each move."""
        gs = GameState.from_fen(self._start_fen)
        in_book = self._start_fen.startswith(GameState().to_fen().split()[0])

        self.evals[0] = evaluate_position(gs, self._max_depth, self._time_limit)
        next_move = 0

        while not self._stop:
            with self._lock:
                pending = next_move < len(self._moves)
                move = self._moves[next_move] if pending else None

            if not pending or move is None:
                # Fully caught up; wait for the GUI to append exploration moves
                time.sleep(0.1)
                continue

            before = self.evals[next_move]
            assert before is not None  # Filled by the previous iteration
            white_moved = gs.white_to_move
            sacrifice = is_sacrifice(gs, move)

            gs.make_ai_move(move)
            # make_ai_move skips the history log (a search-speed optimization),
            # so maintain it by hand: the searches below seed their repetition
            # detection from this list
            gs.zobrist_history.append(gs.zobrist_key)

            in_book = in_book and is_book_position(gs)
            after = evaluate_position(gs, self._max_depth, self._time_limit)
            self.evals[next_move + 1] = after

            second_best: int | None = None
            if needs_uniqueness_check(move, before, after, white_moved, in_book):
                second_best = self._score_best_alternative(next_move, before)

            self.tags[next_move] = classify_move(
                move, before, after, white_moved, in_book, sacrifice, second_best
            )
            next_move += 1

    def _score_best_alternative(self, move_index: int, before: PositionEval) -> int:
        """
        Score `move_index`'s position with its best move excluded.

        The uniqueness search needs the pre-move position back; rebuilding it
        from FEN on a scratch state is simpler and safer than sharing the
        worker's own GameState for a rare, review-only code path.

        Parameters
        ----------
        move_index : int
            Index of the move whose alternatives are being scored.
        before : PositionEval
            The stored evaluation of that position (provides the best move).

        Returns
        -------
        int
            White-perspective score of the best *alternative* move.
        """
        with self._lock:
            replay = self._moves[:move_index]

        rewound = GameState.from_fen(self._start_fen)
        for played in replay:
            rewound.make_ai_move(played)
            rewound.zobrist_history.append(rewound.zobrist_key)

        alternative = evaluate_position(
            rewound, self._max_depth, self._time_limit, exclude=before.best_move
        )
        return alternative.score_white
