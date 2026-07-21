"""
Static evaluation: scoring a position without searching it.

`evaluate()` answers "how good is this position, right now, for White" — the
sign convention is always White's perspective, and the search flips it for
Black. It is material plus piece-square tables, plus the positional terms
below: mobility, pawn structure, rook activity, bishop and knight quality,
king shelter, and a mop-up term for converting won pawnless endgames.

The evaluation is a *pure function of the position*, and `_EVAL_CACHE` depends
on that being true. It is keyed on the Zobrist key alone, so anything that
varies independently of the pieces on the board must not be folded in here.
The 50-move clock is the live example: it is not part of the Zobrist key, so
fading a score toward a draw as that clock runs down happens in the search
(`search._fade_toward_draw`), never in this module.

This module imports from `engine.board` and nothing else in the engine. The
search depends on it; it must never depend on the search.
"""
from engine.board import (
    ALL_DIRECTIONS, BB, BISHOP, BK, BN, BP, BQ, BR, DIAGONAL_DIRECTIONS, EMPTY,
    GameState, KING, KNIGHT, KNIGHT_DELTAS, ORTHOGONAL_DIRECTIONS,
    PAWN, PIECE_TYPE, QUEEN, ROOK, WB, WK, WN, WP, WQ, WR,
)

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

# --- Piece quality -----------------------------------------------------
# Until these, the evaluation understood pawns and rooks but barely knew
# anything about knights and bishops: mobility and a pair bonus, nothing else.
# That is a real blind spot rather than a refinement — a first rated loss turned
# on scoring a bishop-vs-knight endgame move at +37 that Stockfish put ~200cp
# the other way (see docs/ENGINE_V2_PLAN.md Part 8).
#
# A bishop is "bad" in proportion to how many of its *own* pawns sit on the
# squares it can never leave — they block its diagonals and cannot be defended
# by it. Counting own pawns on the bishop's colour is the standard cheap
# formulation. The penalty is per pawn, so a bishop behind a fixed five-pawn
# chain is heavily discounted while one on an open board is untouched.
BAD_BISHOP_PENALTY = 4

# A knight is strongest on a square the enemy can never challenge with a pawn,
# defended by one of ours. Nimzowitsch's original definition also wanted a
# half-open file; engines generally drop that and keep "advanced, defended,
# pawn-safe", which is what this does. The bonus is deliberately smaller than
# the outpost's reputation suggests: it stacks with the piece-square tables,
# which already reward advanced central knights.
KNIGHT_OUTPOST_BONUS = 18
# Rows (from White's view, row 0 = rank 8) where an outpost is worth scoring.
# Rank 4-6 for White is rows 4-2; anything further back is not an outpost, and
# rank 7+ is usually a tactical accident rather than a stable square.
OUTPOST_ROWS_WHITE = (2, 3, 4)
OUTPOST_ROWS_BLACK = (3, 4, 5)

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

# --- Mop-up: converting a won pawnless endgame ---------------------------
# Material plus piece-square tables is *flat* in K+Q vs K: every queen move
# that keeps the queen safe scores the same, so the search has no reason to
# prefer the one that shrinks the enemy king's box. `_root_rng` then shuffles
# among the equal-scored moves and the engine wanders until the 50-move rule
# takes the win away — the "random moves into a draw against a weaker engine"
# the bot actually did online.
#
# The classical cure is two terms, and neither is about the position being
# good; they are about making *progress* measurable. Drive the losing king to
# the edge (it is mated at the rim, never in the center) and walk the winning
# king towards it (almost every basic mate needs the king's help).
#
# Weights are in tenths so the term stays integer. They were *measured*, not
# taken from the textbook: the classic 4.7/1.6 pairing is too quiet against
# this evaluation's other endgame terms (mobility and KING_END_PST are both
# live here), and at 4.7/1.6 K+R vs K still failed to convert 1 run in 12.
# Playing each endgame out over 20 seeded root shuffles:
#
#   corner/prox   K+Q vs K            K+R vs K
#   4.7 / 1.6     12/12  med 47       11/12  med 73
#   8.0 / 1.6     20/20  med 19 max 51    20/20  med 29 max 47
#   12.0 / 3.0    20/20  med 19 max 41    20/20  med 29 max 29
#
# 12.0/3.0 wins on the number that matters — the *worst* case, since the
# 50-move rule is a hard cap and a median says nothing about the run that
# loses the win. The ceiling is 12*6 + 3*13 = ~111cp, which is loud enough to
# steer a flat position but still nowhere near the 400cp the gate already
# requires, so it can never argue with material.
MOPUP_CORNER_WEIGHT = 120         # per unit of the losing king's center distance
MOPUP_KING_PROXIMITY_WEIGHT = 30  # per unit the kings are closer than 14 apart

# Only chase when the advantage is actually mateable. A lone bishop or knight
# cannot mate, and B-vs-N is dead drawn — chasing there would spend the
# 50-move clock on a position that has no win in it. A rook's worth is the
# natural floor: it admits K+R, K+Q and K+B+N, and excludes the rest.
MOPUP_MIN_ADVANTAGE = 400

# Center-manhattan distance: 0 on the four middle squares, 6 in the corners.
# The losing king's mate chances shrink as this grows, which is exactly the
# gradient the search is missing.
_CENTER_MANHATTAN: tuple[tuple[int, ...], ...] = tuple(
    tuple(max(3 - row, row - 4) + max(3 - col, col - 4) for col in range(8))
    for row in range(8)
)

# --- K+B+N vs K, the one mate that needs its own map ---------------------
# Every other basic mate works against *any* corner, which is why the
# symmetric table above is enough for them. Bishop and knight is the
# exception: the bishop only ever controls one square color, so the mate
# only exists in the two corners of that color. Driving the king to the
# wrong corner is not a slower win, it is no win at all — the king walks out
# — so this case needs a table that knows which corner it is aiming for.
#
# The gradient is Stockfish's `PushToCorners`, copied rather than invented.
# A corner-distance formula sounds like it should work and does not: the
# useful gradient also has to pull along the a1-h8 diagonal the bishop
# travels, which is why d4 and e5 score above e4 and d5 here. Getting that
# shape right by hand is exactly the kind of tuning that is already solved.
#
# Indexed the way Stockfish indexes it, a1 = 0 through h8 = 63, and peaking
# at a1 and h8 — both dark. A light-squared bishop mirrors the file, which
# maps those peaks onto h1 and a8.
_PUSH_TO_CORNERS: tuple[int, ...] = (
    6400, 6080, 5760, 5440, 5120, 4800, 4480, 4160,
    6080, 5760, 5440, 5120, 4800, 4480, 4160, 4480,
    5760, 5440, 4960, 4480, 4160, 3840, 4480, 4800,
    5440, 5120, 4480, 3840, 3520, 4160, 4800, 5120,
    5120, 4800, 4160, 3520, 3840, 4480, 5120, 5440,
    4800, 4480, 3840, 4160, 4480, 4960, 5440, 5760,
    4480, 4160, 4480, 4800, 5120, 5440, 5760, 6080,
    4160, 4480, 4800, 5120, 5440, 5760, 6080, 6400,
)
_PUSH_MIN, _PUSH_RANGE = 3520, 6400 - 3520

# Weight in tenths, matching the mop-up terms above. This one is allowed to
# be far louder than they are: in K+B+N vs K there is no other gradient at
# all to compete with, and the mate needs ~33 moves of accurate play against
# a 50-move budget, so a timid pull simply runs out the clock.
#
# Known limitation, measured rather than assumed: this table makes K+B+N vs K
# *better* but still does not convert it reliably. Playing the mate out from
# a standard start, over independent root-shuffle seeds:
#
#   depth 4    never converts
#   depth 6    never converts, even given 110 moves
#   depth 8    4 of 6 seeds, one of those landing on ply 99 of 100
#   depth 10   3 of 3 on one set of seeds, but a fourth seed hit the 50-move
#              rule exactly — so "reliable at depth 10" is not supported
#
# What the table does fix is the direction: before it, the symmetric mop-up
# happily parked the king in a corner the bishop cannot cover, which is not a
# slow win but no win at all. Now the king is driven to a corner the bishop
# controls, and the mate arrives some of the time instead of none of it.
#
# Finishing it properly needs the actual technique (the W-maneuver), which is
# a special-case driver rather than an evaluation term — a gradient cannot
# express "triangulate here". Left undone deliberately: K+B+N did not occur
# once in the 101 recorded games, and lichess-bot answers 4-piece endings from
# the online tablebase (`online_egtb`, max_pieces 8) whenever it has more than
# 20 seconds on the clock, so the engine is rarely asked this question at all.
KBNK_CORNER_WEIGHT = 3000

# Total non-pawn material that identifies K+B+N exactly. With no pawns on the
# board 650 can only be one bishop plus one knight — two bishops make 660,
# two knights 640, and a rook 500.
KBNK_MATERIAL = 650


def _build_kbnk_pull(weight: int) -> tuple[tuple[tuple[int, ...], ...], ...]:
    """
    Precompute the corner pull for both bishop colors, in tenths of a pawn.

    Baked into a table at import so the hot path is a plain lookup rather
    than a rescale. Exposed as a function purely so a tuning run can rebuild
    it at a different weight.

    Parameters
    ----------
    weight : int
        Value in tenths of a centipawn to award at the best corner.

    Returns
    -------
    tuple
        Indexed `[bishop_square_color][row][col]`, where square color is
        `(row + col) & 1` — 1 being the dark color the raw table peaks on.
    """
    return tuple(
        tuple(
            tuple(
                (_PUSH_TO_CORNERS[(7 - row) * 8 + (col if color else 7 - col)]
                 - _PUSH_MIN) * weight // _PUSH_RANGE
                for col in range(8)
            )
            for row in range(8)
        )
        for color in (0, 1)
    )


_KBNK_PULL = _build_kbnk_pull(KBNK_CORNER_WEIGHT)

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
    # Knight and bishop squares, for the outpost and bad-bishop terms. Both
    # need the *finished* pawn picture, so they are scored after these loops
    # rather than inside them.
    white_knights: list[tuple[int, int]] = []
    black_knights: list[tuple[int, int]] = []
    white_bishop_squares: list[int] = []   # square colour, (row + col) & 1
    black_bishop_squares: list[int] = []
    # Own pawns per square colour, indexed [0] dark / [1] light by (row+col)&1.
    white_pawns_on: list[int] = [0, 0]
    black_pawns_on: list[int] = [0, 0]

    for row, col in gs.white_pieces:
        piece = board[row][col]
        piece_type = PIECE_TYPE[piece]
        if piece_type == KING:
            continue
        score += PIECE_VALUES[piece] + PST[piece_type][row][col]
        if piece_type == PAWN:
            white_pawns.append((row, col))
            white_file_pawns[col + 1] += 1
            white_pawns_on[(row + col) & 1] += 1
            if row > white_max_row[col + 1]:
                white_max_row[col + 1] = row
        else:
            non_pawn_material += PIECE_VALUES[piece]
            score += MOBILITY_BONUS[piece_type] * _mobility(board, row, col, piece_type, True)
            if piece_type == BISHOP:
                white_bishops += 1
                white_bishop_squares.append((row + col) & 1)
            elif piece_type == ROOK:
                white_rooks.append((row, col))
            elif piece_type == KNIGHT:
                white_knights.append((row, col))

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
            black_pawns_on[(row + col) & 1] += 1
            if row < black_min_row[col + 1]:
                black_min_row[col + 1] = row
        else:
            non_pawn_material += PIECE_VALUES[piece]
            score -= MOBILITY_BONUS[piece_type] * _mobility(board, row, col, piece_type, False)
            if piece_type == BISHOP:
                black_bishops += 1
                black_bishop_squares.append((row + col) & 1)
            elif piece_type == ROOK:
                black_rooks.append((row, col))
            elif piece_type == KNIGHT:
                black_knights.append((row, col))

    if white_bishops >= 2:
        score += BISHOP_PAIR_BONUS
    if black_bishops >= 2:
        score -= BISHOP_PAIR_BONUS

    # Bad bishop: every own pawn standing on the bishop's own square colour is
    # a pawn it can never defend and a diagonal it can never use.
    for square_colour in white_bishop_squares:
        score -= BAD_BISHOP_PENALTY * white_pawns_on[square_colour]
    for square_colour in black_bishop_squares:
        score += BAD_BISHOP_PENALTY * black_pawns_on[square_colour]

    # Knight outposts: advanced, defended by one of our own pawns, and on a
    # square no enemy pawn can ever attack.
    #
    # White pawns advance towards row 0 and Black's towards row 7, so a *white*
    # knight on (row, col) is challenged by a black pawn arriving at
    # (row - 1, col +- 1). Black pawns only ever move to higher rows, so such a
    # pawn must already sit at or above that square — which is exactly what
    # `black_min_row` records per file. The same reasoning mirrors for Black.
    for row, col in white_knights:
        if row not in OUTPOST_ROWS_WHITE:
            continue
        defended = ((row + 1 < 8 and col > 0 and board[row + 1][col - 1] == WP)
                    or (row + 1 < 8 and col < 7 and board[row + 1][col + 1] == WP))
        if not defended:
            continue
        # Sentinel-padded lists, so col-1 and col+1 need no bounds checks.
        if black_min_row[col] > row - 1 and black_min_row[col + 2] > row - 1:
            score += KNIGHT_OUTPOST_BONUS

    for row, col in black_knights:
        if row not in OUTPOST_ROWS_BLACK:
            continue
        defended = ((row - 1 >= 0 and col > 0 and board[row - 1][col - 1] == BP)
                    or (row - 1 >= 0 and col < 7 and board[row - 1][col + 1] == BP))
        if not defended:
            continue
        if white_max_row[col] < row + 1 and white_max_row[col + 2] < row + 1:
            score -= KNIGHT_OUTPOST_BONUS

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

    # Mop-up. Gated on there being no pawns at all, which is both the case
    # where the evaluation goes flat and a condition already in hand from the
    # loops above — so the common case pays one `if` on two lists and nothing
    # else. Per-side material is only counted inside the gate, where the piece
    # sets are a handful of entries by definition.
    if not white_pawns and not black_pawns:
        white_material = sum(PIECE_VALUES[board[r][c]] for r, c in gs.white_pieces)
        black_material = sum(PIECE_VALUES[board[r][c]] for r, c in gs.black_pieces)
        advantage = white_material - black_material
        if advantage >= MOPUP_MIN_ADVANTAGE:
            loser_row, loser_col = bk_row, bk_col
            winner_material, loser_material = white_material, black_material
            winner_bishops = white_bishop_squares
            sign = 1
        elif -advantage >= MOPUP_MIN_ADVANTAGE:
            loser_row, loser_col = wk_row, wk_col
            winner_material, loser_material = black_material, white_material
            winner_bishops = black_bishop_squares
            sign = -1
        else:
            sign = 0
        if sign:
            proximity = 14 - (abs(wk_row - bk_row) + abs(wk_col - bk_col))
            if (loser_material == 0 and winner_material == KBNK_MATERIAL
                    and winner_bishops):
                # Bishop and knight: aim at the corners this bishop can
                # actually cover, not at whichever one happens to be nearest.
                pull = _KBNK_PULL[winner_bishops[0]][loser_row][loser_col]
            else:
                pull = MOPUP_CORNER_WEIGHT * _CENTER_MANHATTAN[loser_row][loser_col]
            score += sign * (
                pull + MOPUP_KING_PROXIMITY_WEIGHT * proximity
            ) // 10

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
