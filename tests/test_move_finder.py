"""
Test suite covering the AI search in move_finder: tactical correctness
(mates, captures), state preservation, evaluation symmetry, and the
UCI adapter helpers.
"""
from engine import move_finder
from engine import uci
from engine.chess_engine import GameState, Move


# --- Search finds forced wins ---
def test_finds_mate_in_one_back_rank():
    """Verify the search plays the immediate back-rank mate."""
    gs = GameState.from_fen('6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1')
    best = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'a1a8'


def test_finds_mate_in_one_as_black():
    """Verify mate detection works symmetrically for Black."""
    gs = GameState.from_fen('r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1')
    best = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert Move.from_ai_tuple(best, gs.board).get_uci_notation() == 'a8a1'


def test_captures_hanging_queen():
    """Verify the search grabs an undefended queen."""
    gs = GameState.from_fen('6k1/8/8/3q4/8/3R4/8/6K1 w - - 0 1')
    best = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert (best[2], best[3]) == (3, 3)  # Rxd5


def test_avoids_losing_queen_to_recapture():
    """Verify quiescence stops the queen from taking a defended pawn."""
    # Black pawn d5 is defended by the pawn on e6; Qxd5 would lose the queen
    gs = GameState.from_fen('6k1/8/4p3/3p4/8/8/3Q4/6K1 w - - 0 1')
    best = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0)
    assert best is not None
    assert (best[2], best[3]) != (3, 3)


# --- Search interface behavior ---
def test_returns_none_when_no_legal_moves():
    """Verify find_best_move returns None for a checkmated position."""
    # Fool's mate final position: White is mated, White to move
    gs = GameState.from_fen('rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3')
    assert move_finder.find_best_move(gs, max_depth=2, time_limit=5.0) is None


def test_respects_precalculated_move_list(gs):
    """Verify the search only considers moves from a supplied root list."""
    only_move = (6, 0, 5, 0, 0)  # a3 as the single allowed root move
    best = move_finder.find_best_move(gs, valid_moves=[only_move], max_depth=2, time_limit=5.0)
    assert best == only_move


def test_search_restores_game_state(gs):
    """Verify the search leaves the position exactly as it found it."""
    board_before = [row[:] for row in gs.board]
    key_before = gs.zobrist_key
    white_to_move_before = gs.white_to_move

    move_finder.find_best_move(gs, max_depth=3, time_limit=10.0)

    assert gs.board == board_before
    assert gs.zobrist_key == key_before
    assert gs.white_to_move == white_to_move_before


# --- Evaluation ---
def test_evaluate_start_position_is_balanced(gs):
    """Verify the symmetric initial position evaluates to exactly zero."""
    assert move_finder.evaluate(gs) == 0


def test_evaluate_material_advantage():
    """Verify an extra queen dominates any positional table bonuses."""
    gs = GameState.from_fen('6k1/8/8/8/8/8/3Q4/6K1 w - - 0 1')
    assert move_finder.evaluate(gs) > 500

    gs = GameState.from_fen('3q2k1/8/8/8/8/8/8/6K1 w - - 0 1')
    assert move_finder.evaluate(gs) < -500


def test_search_position_reports_score(gs):
    """Verify the score-returning search agrees with find_best_move."""
    move, score = move_finder.search_position(gs, max_depth=2, time_limit=5.0)
    assert move is not None
    # The symmetric start position should stay close to balanced
    assert abs(score) < 100


# --- UCI adapter helpers ---
def test_uci_build_position_startpos_with_moves():
    """Verify 'position startpos moves ...' replays UCI moves correctly."""
    gs = uci.build_position(['startpos', 'moves', 'e2e4', 'e7e5', 'g1f3'])
    assert gs.board[4][4] == 'wP'  # e4
    assert gs.board[3][4] == 'bP'  # e5
    assert gs.board[5][5] == 'wN'  # Nf3
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


# --- Step 6 evaluation terms ---

def test_insufficient_material_scores_zero(custom_gs):
    """Verify dead-drawn material evaluates to exactly zero."""
    for extra in ((), ((4, 4, 'wN'),), ((4, 4, 'wN'), (3, 3, 'bB'))):
        empty_board = [['--' for _ in range(8)] for _ in range(8)]
        empty_board[7][4] = 'wK'
        empty_board[0][4] = 'bK'
        for row, col, piece in extra:
            empty_board[row][col] = piece
        assert move_finder.evaluate(custom_gs(empty_board)) == move_finder.DRAW_SCORE


def test_two_knights_draw_but_two_bishops_not(custom_gs):
    """Verify the KNN vs K special case: two knights cannot force mate,
    while two bishops (or a rook, etc.) very much can."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    empty_board[4][2] = 'wN'
    empty_board[4][5] = 'wN'
    assert move_finder.evaluate(custom_gs(empty_board)) == move_finder.DRAW_SCORE

    empty_board[4][2] = 'wB'
    empty_board[4][5] = 'wB'
    assert move_finder.evaluate(custom_gs(empty_board)) > 0


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
    one_bishop = move_finder.evaluate(custom_gs(empty_board))

    empty_board[4][5] = 'wB'
    two_bishops = move_finder.evaluate(custom_gs([row[:] for row in empty_board]))

    expected_gain = (move_finder.PIECE_VALUES['B'] + move_finder.PST['B'][4][5]
                     + move_finder.BISHOP_PAIR_BONUS)
    assert two_bishops - one_bishop == expected_gain


def test_passed_pawn_bonus(custom_gs):
    """Verify a lone advanced pawn earns its passed bonus, and that a black
    pawn ahead on an adjacent file takes it away."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    empty_board[3][4] = 'wP'  # e5, no black pawns anywhere: passed
    passed = move_finder.evaluate(custom_gs([row[:] for row in empty_board]))

    empty_board[2][3] = 'bP'  # d6 guards the e-pawn's path: no longer passed
    blocked = move_finder.evaluate(custom_gs(empty_board))

    # The delta is the black pawn's own value/PST plus the lost passed bonus
    # (the d6 pawn itself is not passed: the e5 pawn stands ahead of it)
    expected_delta = (move_finder.PIECE_VALUES['P'] + move_finder.PST['P'][5][3]
                      + move_finder.PASSED_PAWN_BONUS[3])
    assert passed - blocked == expected_delta


def test_king_pawn_shield_bonus(custom_gs):
    """Verify a castled king's pawn cover counts in the middlegame.

    Queens and rooks keep the position above the endgame threshold; the only
    difference between the two evaluations is the g-pawn standing on g2
    (shielding) versus g4 (not shielding), so the delta is its PST change
    plus one shield bonus.
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

    shielded = move_finder.evaluate(custom_gs(shielded_board))
    advanced = move_finder.evaluate(custom_gs(advanced_board))

    expected_delta = (move_finder.PST['P'][6][6] - move_finder.PST['P'][4][6]
                      + move_finder.KING_SHIELD_BONUS)
    assert shielded - advanced == expected_delta


# --- Step 6 persistent transposition table ---

def test_persistent_tt_reused_across_searches(gs):
    """Verify a caller-held table survives between searches and keeps
    growing instead of being rebuilt from scratch."""
    tt: move_finder.TTable = {}
    first = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0, tt=tt)
    assert first is not None
    assert len(tt) > 0

    size_after_first = len(tt)
    gs.make_ai_move(first)
    second = move_finder.find_best_move(gs, max_depth=3, time_limit=10.0, tt=tt)
    assert second in gs.get_valid_moves(for_ai=True)
    assert len(tt) >= size_after_first


def test_uci_go_fills_and_newgame_clears_the_tt():
    """Verify handle_go populates the adapter's game-long table (and that
    clearing it — what ucinewgame does — leaves the adapter functional)."""
    uci.transposition_table.clear()
    gs = GameState()
    uci.handle_go(gs, ['depth', '2'])
    assert len(uci.transposition_table) > 0

    uci.transposition_table.clear()
    assert uci.handle_go(gs, ['depth', '1']) != ''
