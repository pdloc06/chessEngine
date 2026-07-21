"""
Test suite covering the AI search in move_finder: tactical correctness
(mates, captures), state preservation, evaluation symmetry, and the
UCI adapter helpers.
"""
import time

from engine import uci
from engine.board import (
    GameState, Move, PAWN, BISHOP, QUEEN, WP, WN, WB, WR, WQ, BP,
)
from engine.movegen import generate_legal
from engine.eval import BISHOP_PAIR_BONUS, CHECKMATE_SCORE, DRAW_SCORE, ISOLATED_PAWN_PENALTY, KING_SHIELD_BONUS, MATE_THRESHOLD, MOBILITY_BONUS, PASSED_PAWN_BONUS_END, PHASE_MATERIAL_MAX, PHASE_MAX, PIECE_VALUES, PST, _EVAL_CACHE, _mobility, evaluate
from engine import search
from engine.search import (
    HALFMOVE_DRAW_LIMIT, HALFMOVE_FADE_START, _fade_toward_draw, _root_rng,
    _see, find_best_move, search_position,
)
from engine.tt import TTable


# --- Search finds forced wins ---
def test_finds_mate_in_one_back_rank():
    """Verify the search plays the immediate back-rank mate."""
    gs = GameState.from_fen('6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1')
    best = find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'a1a8'


def test_finds_mate_in_one_as_black():
    """Verify mate detection works symmetrically for Black."""
    gs = GameState.from_fen('r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1')
    best = find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'a8a1'


def test_captures_hanging_queen():
    """Verify the search grabs an undefended queen."""
    gs = GameState.from_fen('6k1/8/8/3q4/8/3R4/8/6K1 w - - 0 1')
    best = find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert (best[2], best[3]) == (3, 3)  # Rxd5


def test_avoids_losing_queen_to_recapture():
    """Verify quiescence stops the queen from taking a defended pawn."""
    # Black pawn d5 is defended by the pawn on e6; Qxd5 would lose the queen
    gs = GameState.from_fen('6k1/8/4p3/3p4/8/8/3Q4/6K1 w - - 0 1')
    best = find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert (best[2], best[3]) != (3, 3)


def test_side_to_move_in_check_after_ai_move():
    """Verify a checking move is detectable straight after ``make_ai_move()``.

    The search must know whether the move it just made gives check, so it can
    refuse to skip it. The cached ``in_check`` attribute cannot answer that:
    only ``get_valid_moves()`` refreshes it, so after ``make_ai_move()`` it
    still describes the *parent* position. This pins the distinction — the
    search once read the stale flag, which silently disabled the guard meant
    to keep forcing checks out of the futility skip.
    """
    # White queen d1 -> d8 delivers check to the black king on e8.
    gs = GameState.from_fen('4k3/8/8/8/8/8/8/3QK3 w - - 0 1')
    stale_before = gs.in_check
    gs.make_ai_move((7, 3, 0, 3, 0))
    assert gs.side_to_move_in_check() is True
    # The cached flag did not move: it is still the pre-move value.
    assert gs.in_check == stale_before


# --- Search-strength regressions (guard LMR / aspiration / SEE) ---
def test_finds_mate_via_quiet_key_move():
    """Verify the search still finds a forced mate whose first move is quiet.

    Two rooks vs a lone king: the only path to mate is a *quiet* rook lift
    (1.Ra7, threatening 2.Rb8#) — no capture, no check. That is exactly the
    kind of move late-move reductions search shallower, so this position is the
    canary that LMR (and later pruning) never reduce a mating idea out of view.
    """
    gs = GameState.from_fen('7k/8/8/8/8/8/R7/1R5K w - - 0 1')
    move, score = search_position(gs, max_depth=4, time_limit=10.0)
    assert move is not None
    assert score >= MATE_THRESHOLD


def test_finds_knight_fork_winning_queen():
    """Verify a depth-reachable fork that wins material is still found.

    Black's knight on d4 forks the white king on g1 and queen on e1 with
    Nf3+; the material swing only shows up a few plies deep, past where LMR
    starts reducing quiet moves.
    """
    gs = GameState.from_fen('6k1/8/8/8/3n4/8/8/4Q1K1 b - - 0 1')
    best = find_best_move(gs, max_depth=4, time_limit=10.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'd4f3'


def test_aspiration_widens_on_large_swing():
    """Verify a winning move far outside the aspiration window is still found.

    The fork wins a whole queen — a score jump of hundreds of centipawns, well
    past ASPIRATION_DELTA — so at a deep iteration the narrow window fails high
    and must widen and re-search to surface the move. Searching to depth 5 runs
    several aspiration iterations, exercising that widen-and-retry path.
    """
    gs = GameState.from_fen('6k1/8/8/8/3n4/8/8/4Q1K1 b - - 0 1')
    best = find_best_move(gs, max_depth=5, time_limit=15.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'd4f3'


# --- Static exchange evaluation ---
def _see_of(fen, uci):
    """Helper: the SEE value of the move written in UCI from the given FEN."""
    gs = GameState.from_fen(fen)
    move = next(
        m for m in generate_legal(gs, for_ai=True)
        if Move.from_ai_tuple(m, gs.board).get_uci_notation() == uci
    )
    return _see(gs, move)


def test_see_wins_undefended_pawn():
    """Verify capturing an undefended pawn nets its full value."""
    assert _see_of('6k1/8/8/3p4/8/3R4/8/6K1 w - - 0 1', 'd3d5') == 100


def test_see_loses_queen_for_defended_pawn():
    """Verify taking a defended pawn with the queen scores the net loss."""
    # Black pawn d5 is defended by the e6 pawn: Qxd5 wins a pawn but loses the
    # queen to the recapture, netting 100 - 900 = -800.
    assert _see_of('6k1/8/4p3/3p4/8/8/3Q4/6K1 w - - 0 1', 'd2d5') == -800


def test_see_counts_xray_recapture():
    """Verify the swap-off reveals a second rook stacked behind the first.

    Rd3 takes the pawn on d5 (defended by e6); after ...exd5 the *back* rook on
    d2 X-rays through the now-vacated d3 to recapture. White ends up trading a
    rook for two pawns: 100 - 500 + 100 = -300.
    """
    assert _see_of('6k1/8/4p3/3p4/8/3R4/3R4/6K1 w - - 0 1', 'd3d5') == -300


# --- Search interface behavior ---
def test_returns_none_when_no_legal_moves():
    """Verify find_best_move returns None for a checkmated position."""
    # Fool's mate final position: White is mated, White to move
    gs = GameState.from_fen('rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3')
    assert find_best_move(gs, max_depth=2, time_limit=5.0) is None


def test_respects_precalculated_move_list(gs):
    """Verify the search only considers moves from a supplied root list."""
    only_move = (6, 0, 5, 0, 0)  # a3 as the single allowed root move
    best = find_best_move(gs, valid_moves=[only_move], max_depth=2, time_limit=5.0)
    assert best == only_move


def test_search_restores_game_state(gs):
    """Verify the search leaves the position exactly as it found it."""
    board_before = [row[:] for row in gs.board]
    key_before = gs.zobrist_key
    white_to_move_before = gs.white_to_move

    find_best_move(gs, max_depth=3, time_limit=10.0)

    assert gs.board == board_before
    assert gs.zobrist_key == key_before
    assert gs.white_to_move == white_to_move_before


# --- Evaluation ---
def test_evaluate_start_position_is_balanced(gs):
    """Verify the symmetric initial position evaluates to exactly zero."""
    assert evaluate(gs) == 0


def test_evaluate_material_advantage():
    """Verify an extra queen dominates any positional table bonuses."""
    gs = GameState.from_fen('6k1/8/8/8/8/8/3Q4/6K1 w - - 0 1')
    assert evaluate(gs) > 500

    gs = GameState.from_fen('3q2k1/8/8/8/8/8/8/6K1 w - - 0 1')
    assert evaluate(gs) < -500


def test_search_position_reports_score(gs):
    """Verify the score-returning search agrees with find_best_move."""
    move, score = search_position(gs, max_depth=2, time_limit=5.0)
    assert move is not None
    # The symmetric start position should stay close to balanced
    assert abs(score) < 100


# --- UCI adapter helpers ---
def test_uci_build_position_startpos_with_moves():
    """Verify 'position startpos moves ...' replays UCI moves correctly."""
    gs = uci.build_position(['startpos', 'moves', 'e2e4', 'e7e5', 'g1f3'])
    assert gs.board[4][4] == WP  # e4
    assert gs.board[3][4] == BP  # e5
    assert gs.board[5][5] == WN  # Nf3
    assert gs.white_to_move is False


def test_uci_build_position_from_fen():
    """Verify 'position fen ...' parses the position and side to move."""
    fen = '6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1'
    gs = uci.build_position(['fen'] + fen.split())
    assert gs.to_fen() == fen


def test_uci_go_reports_mate_move():
    """Verify handle_go returns the mating move in UCI notation."""
    gs = GameState.from_fen('6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1')
    assert uci.handle_go(gs, ['depth', '3']) == 'a1a8'


def test_uci_apply_illegal_move_rejected(gs):
    """Verify apply_uci_move refuses moves that are not legal."""
    assert uci.apply_uci_move(gs, 'e2e5') is False
    assert uci.apply_uci_move(gs, 'e2e4') is True


# --- Evaluation terms ---

def test_insufficient_material_scores_zero(custom_gs):
    """Verify dead-drawn material evaluates to exactly zero."""
    for extra in ((), ((4, 4, 'wN'),), ((4, 4, 'wN'), (3, 3, 'bB'))):
        empty_board = [['--' for _ in range(8)] for _ in range(8)]
        empty_board[7][4] = 'wK'
        empty_board[0][4] = 'bK'
        for row, col, piece in extra:
            empty_board[row][col] = piece
        assert evaluate(custom_gs(empty_board)) == DRAW_SCORE


def test_two_knights_draw_but_two_bishops_not(custom_gs):
    """Verify the KNN vs K special case: two knights cannot force mate,
    while two bishops (or a rook, etc.) very much can."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    empty_board[4][2] = 'wN'
    empty_board[4][5] = 'wN'
    assert evaluate(custom_gs(empty_board)) == DRAW_SCORE

    empty_board[4][2] = 'wB'
    empty_board[4][5] = 'wB'
    assert evaluate(custom_gs(empty_board)) > 0


def test_bishop_pair_bonus(custom_gs):
    """Verify the second bishop is worth its material + PST + pair bonus.

    A black pawn keeps the material "sufficient" so the insufficient-material
    shortcut can't zero either evaluation.
    """
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    empty_board[1][0] = 'bP'
    empty_board[4][2] = 'wB'
    one_bishop = evaluate(custom_gs(empty_board))

    empty_board[4][5] = 'wB'
    gs_two = custom_gs([row[:] for row in empty_board])
    two_bishops = evaluate(gs_two)

    # The new bishop also brings its own mobility bonus. It stands on none of
    # the first bishop's diagonals, so that bishop's mobility (and every other
    # piece's) is identical in both positions and cancels out of the delta.
    expected_gain = (PIECE_VALUES[WB] + PST[BISHOP][4][5]
                     + BISHOP_PAIR_BONUS
                     + MOBILITY_BONUS[BISHOP]
                     * _mobility(gs_two.board, 4, 5, BISHOP, True))
    assert two_bishops - one_bishop == expected_gain


def test_passed_pawn_bonus(custom_gs):
    """Verify a lone advanced pawn earns its passed bonus, and that a black
    pawn ahead on an adjacent file takes it away."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    empty_board[3][4] = 'wP'  # e5, no black pawns anywhere: passed
    passed = evaluate(custom_gs([row[:] for row in empty_board]))

    empty_board[2][3] = 'bP'  # d6 guards the e-pawn's path: no longer passed
    blocked = evaluate(custom_gs(empty_board))

    # The delta is the black pawn's own value/PST plus the lost passed bonus
    # (the d6 pawn itself is not passed: the e5 pawn stands ahead of it),
    # minus the isolated-pawn penalty the lone d6 pawn drags with it. The
    # white e-pawn is equally isolated in both positions, so its penalty
    # cancels out of the difference. With only kings and pawns on the board
    # the tapered phase is 0, so the *endgame* passed-pawn column applies
    # in full.
    expected_delta = (PIECE_VALUES[WP] + PST[PAWN][5][3]
                      + PASSED_PAWN_BONUS_END[3]
                      - ISOLATED_PAWN_PENALTY)
    assert passed - blocked == expected_delta


def test_king_pawn_shield_bonus(custom_gs):
    """Verify a castled king's pawn cover counts while attackers remain.

    Queens and rooks keep the tapered phase well above zero; the only
    difference between the two evaluations is the g-pawn standing on g2
    (shielding) versus g4 (not shielding), so the delta is its PST change
    plus one shield bonus scaled by the position's phase.
    """
    base = [['--' for _ in range(8)] for _ in range(8)]
    base[7][6] = 'wK'  # g1
    base[0][4] = 'bK'  # e8
    base[7][3] = 'wQ'
    base[0][3] = 'bQ'
    base[7][0] = 'wR'
    base[0][0] = 'bR'
    base[1][6] = 'bP'  # g7 keeps the white g-pawn from being "passed"

    shielded_board = [row[:] for row in base]
    shielded_board[6][6] = 'wP'  # g2
    advanced_board = [row[:] for row in base]
    advanced_board[4][6] = 'wP'  # g4

    gs_shielded = custom_gs(shielded_board)
    gs_advanced = custom_gs(advanced_board)
    shielded = evaluate(gs_shielded)
    advanced = evaluate(gs_advanced)

    # Phase from the non-pawn material actually on the board (Q + R each
    # side), exactly as evaluate() computes it; the shield bonus is scaled
    # by phase/PHASE_MAX before the integer division floors it.
    non_pawn = 2 * (PIECE_VALUES[WQ] + PIECE_VALUES[WR])
    phase = min(PHASE_MAX,
                non_pawn * PHASE_MAX // PHASE_MATERIAL_MAX)
    # The g-pawn's square also changes the white queen's mobility: on g4 it
    # blocks her d1-h5 diagonal, on g2 it doesn't. No other piece's lines
    # cross either pawn square, so the queen is the only mobility difference
    # between the two boards.
    mobility_delta = MOBILITY_BONUS[QUEEN] * (
        _mobility(gs_shielded.board, 7, 3, QUEEN, True)
        - _mobility(gs_advanced.board, 7, 3, QUEEN, True))
    expected_delta = (PST[PAWN][6][6] - PST[PAWN][4][6]
                      + KING_SHIELD_BONUS * phase // PHASE_MAX
                      + mobility_delta)
    assert shielded - advanced == expected_delta


# --- Persistent transposition table ---

def test_persistent_tt_reused_across_searches(gs):
    """Verify a caller-held table survives between searches and keeps
    growing instead of being rebuilt from scratch."""
    tt: TTable = {}
    first = find_best_move(gs, max_depth=3, time_limit=10.0, tt=tt)
    assert first is not None
    assert len(tt) > 0

    size_after_first = len(tt)
    gs.make_ai_move(first)
    second = find_best_move(gs, max_depth=3, time_limit=10.0, tt=tt)
    assert second in generate_legal(gs, for_ai=True)
    assert len(tt) >= size_after_first


def test_tt_entries_carry_generation_and_age(gs):
    """Verify each TT entry stamps its search generation and that a later,
    deeper search ages entries forward (the replacement policy's aging)."""
    tt: TTable = {}
    find_best_move(gs, max_depth=3, time_limit=10.0, tt=tt)
    assert all(len(entry) == 5 for entry in tt.values())
    gen_first = max(entry[4] for entry in tt.values())

    # Re-search deeper so cached entries are re-stored rather than merely hit;
    # the deeper results carry the newer generation, letting stale entries from
    # earlier moves lose ties and age out over the course of a game.
    find_best_move(gs, max_depth=4, time_limit=10.0, tt=tt)
    gen_second = max(entry[4] for entry in tt.values())
    assert gen_second > gen_first
    assert any(entry[4] == gen_second for entry in tt.values())


def test_uci_go_fills_and_newgame_clears_the_tt():
    """Verify handle_go populates the adapter's game-long table (and that
    clearing it — what ucinewgame does — leaves the adapter functional)."""
    uci.transposition_table.clear()
    gs = GameState()
    uci.handle_go(gs, ['depth', '2'])
    assert len(uci.transposition_table) > 0

    uci.transposition_table.clear()
    assert uci.handle_go(gs, ['depth', '1']) != ''


def test_node_count_is_reproducible_for_a_seeded_search():
    """
    Verify the property the benchmark's whole value rests on.

    `engine.tools.bench` compares engine versions by node count rather than by
    playing games, because a node count carries no measurement noise — but
    that only holds if a repeated search really does visit the same nodes.
    Two things can break it: the root move shuffle (`_root_rng`), which
    changes how much the search prunes, and the evaluation cache, which
    carries work across searches. Pin the first and clear the second, and
    the counts must match exactly.
    """
    fen = 'r2q1rk1/1p1n1ppp/p1pbpn2/8/2BP4/2N1PN2/PP3PPP/R1BQ1RK1 w - - 0 11'

    def seeded_search() -> int:
        _root_rng.seed(1234)
        _EVAL_CACHE.clear()
        gs = GameState.from_fen(fen)
        find_best_move(gs, max_depth=4, time_limit=60.0)
        return search.last_search_nodes

    first = seeded_search()
    assert first > 0
    assert seeded_search() == first


def test_search_spends_most_of_its_time_budget():
    """
    Guard the fix for a search that used to stop with a third of its clock left.

    Two rules combined to waste it: an aborted iteration was discarded whole,
    so the soft-stop gate refused to *start* one it could not finish. Measured,
    that spent 58% of a 3s budget. Now `_search_root` keeps whatever an aborted
    iteration proved -- root moves are ordered best-first, so a partial pass
    either confirms the previous best or replaces it with a move that beat it a
    ply deeper -- and the gate can sit near the full budget.

    A generous floor keeps this from being a flaky timing test: anything above
    the old ~58% shows the mechanism works, and the check is only meaningful on
    a position too complex to solve outright (which would legitimately return
    early on a mate score).
    """
    gs = GameState.from_fen(
        'r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2N1PN2/PP2BPPP/R1BQ1RK1 w - - 0 10')
    budget = 1.0
    start = time.perf_counter()
    best = find_best_move(gs, max_depth=64, time_limit=budget)
    elapsed = time.perf_counter() - start

    assert best is not None
    assert elapsed > budget * 0.75, f'used only {elapsed:.2f}s of {budget}s'
    # And it must not run away with the clock: overrunning a real budget is
    # how an engine flags.
    assert elapsed < budget * 1.5, f'overran: {elapsed:.2f}s of {budget}s'


# --- The 50-move rule, as the search sees it ---
def test_fade_leaves_scores_alone_early():
    """Below the fade threshold the clock must not touch the score at all."""
    for clock in (0, 20, HALFMOVE_FADE_START):
        assert _fade_toward_draw(300, clock) == 300
        assert _fade_toward_draw(-300, clock) == -300


def test_fade_shrinks_scores_as_the_clock_runs_out():
    """
    Between the fade start and the limit the score decays toward a draw,
    monotonically and without ever changing sign.
    """
    previous = 300
    for clock in range(HALFMOVE_FADE_START + 1,
                       HALFMOVE_DRAW_LIMIT + 1):
        faded = _fade_toward_draw(300, clock)
        assert 0 <= faded <= previous
        previous = faded
    assert previous == 0


def test_fade_never_flips_a_score_past_the_limit():
    """
    Quiescence has no 50-move guard of its own, so it can be handed a clock
    past the limit. An unclamped multiplier would invert the score there.
    """
    assert _fade_toward_draw(300, 140) == 0
    assert _fade_toward_draw(-300, 140) == 0


def test_fade_leaves_mate_scores_intact():
    """
    A proved mate is unaffected by the 50-move clock, and scaling it would
    corrupt the mate-distance ordering that picks the fastest mate.
    """
    mate = CHECKMATE_SCORE - 5
    assert _fade_toward_draw(mate, 99) == mate
    assert _fade_toward_draw(-mate, 99) == -mate


def test_search_scores_a_dead_fifty_move_position_as_a_draw():
    """
    A whole extra queen is worth nothing when the 50-move clock has expired.
    Before the search tracked `halfmove_clock` it scored this near +900 and
    shuffled on, which is exactly how won games were drawn.
    """
    fen = '8/8/4k3/8/8/4K3/8/6Q1 w - - 100 80'
    gs = GameState.from_fen(fen)
    assert gs.halfmove_clock == 100
    _move, score = search_position(gs, max_depth=3, time_limit=5.0)
    assert score == DRAW_SCORE
