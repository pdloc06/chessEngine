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
