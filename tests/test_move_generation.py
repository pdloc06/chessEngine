"""
Test suite covering piece movement logic, move validity,
and special mechanics (En Passant, Castling, Promotion).
"""
import random

from engine.chess_engine import GameState, Move, EMPTY


def test_pawn_moves(gs):
    """Verify standard 1-square and 2-square pawn advances."""
    valid_moves = gs.get_valid_moves()

    move_2_squares = Move((6, 4), (4, 4), gs.board)
    move_1_square = Move((6, 4), (5, 4), gs.board)

    assert move_2_squares in valid_moves
    assert move_1_square in valid_moves


def test_knight_jumps_over_pieces(gs):
    """Verify knights can jump over friendly and enemy pieces."""
    valid_moves = gs.get_valid_moves()
    knight_move = Move((7, 6), (5, 5), gs.board)
    assert knight_move in valid_moves


def test_sliding_piece_blocked(custom_gs):
    """Verify sliding pieces (Rooks, Bishops, Queens) are blocked by other pieces."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[4][4] = 'wR'  # White rook on e4
    empty_board[4][5] = 'wP'  # White pawn on f4 blocks the right side
    empty_board[0][0] = 'bK'
    empty_board[7][7] = 'wK'

    gs = custom_gs(empty_board)
    valid_moves = gs.get_valid_moves()

    blocked_move = Move((4, 4), (4, 6), gs.board)
    assert blocked_move not in valid_moves


def test_pawn_promotion(custom_gs):
    """Verify pawn is allowed to promote upon reaching the final rank."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[1][0] = 'wP'  # White pawn on a7
    empty_board[0][4] = 'bK'
    empty_board[7][4] = 'wK'

    gs = custom_gs(empty_board)
    valid_moves = gs.get_valid_moves()
    promotion_move = Move.promotion((1, 0), (0, 0), gs.board)

    assert promotion_move in valid_moves


def test_en_passant(gs):
    """Verify standard en passant capture sequence."""
    gs.make_move(Move((6, 4), (4, 4), gs.board))  # wP e4
    gs.make_move(Move((1, 0), (2, 0), gs.board))  # bP a6 (dummy)
    gs.make_move(Move((4, 4), (3, 4), gs.board))  # wP e5
    gs.make_move(Move((1, 3), (3, 3), gs.board))  # bP d5

    valid_moves = gs.get_valid_moves()
    en_passant_move = Move.en_passant((3, 4), (2, 3), gs.board)
    assert en_passant_move in valid_moves


def test_en_passant_horizontal_pin_bug(custom_gs):
    """
    Edge Case: Prevent en passant if removing the capturing and captured pawns
    exposes the King to a horizontal attack.
    """
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[3][0] = 'bR'  # Rook on a5
    empty_board[3][7] = 'wK'  # King on h5
    empty_board[3][5] = 'wP'  # White Pawn on f5
    empty_board[1][6] = 'bP'  # Black Pawn on g7

    gs = custom_gs(empty_board, white_turn=False)
    gs.make_move(Move((1, 6), (3, 6), gs.board))  # Black moves g7-g5

    valid_moves = gs.get_valid_moves()
    en_passant_move = Move.en_passant((3, 5), (2, 6), gs.board)
    assert en_passant_move not in valid_moves


def test_queenside_castling_with_b1_attacked(custom_gs):
    """
    Edge Case: Castling queenside is valid even if the b1/b8 square is attacked,
    as long as the King does not pass through or land on an attacked square.
    """
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'  # e1
    empty_board[7][0] = 'wR'  # a1
    empty_board[0][4] = 'bK'  # e8
    empty_board[1][1] = 'bQ'  # Black Queen attacks b1 square

    gs = custom_gs(empty_board)
    gs.white_castle_queen_side = True

    valid_moves = gs.get_valid_moves()
    castle_move = Move.castle((7, 4), (7, 2), gs.board)
    assert castle_move in valid_moves


def test_castling_blocked_by_attack(custom_gs):
    """Verify castling is blocked if an intermediate square is under attack."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[7][7] = 'wR'
    empty_board[0][5] = 'bR'  # Black rook attacks f1
    empty_board[0][0] = 'bK'

    gs = custom_gs(empty_board)
    gs.white_castle_king_side = True

    valid_moves = gs.get_valid_moves()
    castle_move = Move.castle((7, 4), (7, 6), gs.board)
    assert castle_move not in valid_moves


def test_lose_castling_rights_on_king_move(custom_gs):
    """Verify moving the King permanently revokes castling rights."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[7][0] = 'wR'
    empty_board[7][7] = 'wR'
    empty_board[0][4] = 'bK'

    gs = custom_gs(empty_board)
    gs.white_castle_king_side = True
    gs.white_castle_queen_side = True

    gs.make_move(Move((7, 4), (6, 4), gs.board))  # King moves Ke2
    assert gs.white_castle_king_side is False
    assert gs.white_castle_queen_side is False

# --- Captures-only generation (quiescence fast path) ---

def _noisy_reference(gs):
    """Reference implementation: filter the full legal AI move list down to
    the "noisy" subset (captures, en passant, promotions) the way the
    quiescence search did before the dedicated captures-only generator."""
    board = gs.board
    return {
        m for m in gs.get_valid_moves(for_ai=True)
        if board[m[2]][m[3]] != EMPTY or m[4] == 2 or m[4] >= 3
    }


def test_captures_only_matches_filter_on_kiwipete():
    """Verify the captures-only generator agrees with filtering the full
    move list in Kiwipete, the classic generation stress position."""
    gs = GameState.from_fen(
        'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1'
    )
    assert set(gs.get_valid_moves(for_ai=True, captures_only=True)) == _noisy_reference(gs)


def test_captures_only_matches_filter_along_random_walk(gs):
    """Verify captures-only equivalence at every position of a random game.

    This is the same safety-net idea as the AI-interface random walk: any
    divergence between the two generation paths (a missed capture, a leaked
    quiet move) surfaces as a set mismatch within a few dozen plies.
    """
    rng = random.Random(11)
    for _ in range(60):
        assert set(gs.get_valid_moves(for_ai=True, captures_only=True)) == _noisy_reference(gs)
        moves = gs.get_valid_moves(for_ai=True)
        if not moves:
            break
        gs.make_ai_move(rng.choice(moves))


def test_captures_only_returns_all_evasions_in_check():
    """Verify that in check the flag is ignored and every evasion comes back
    — quiet ones included — so an empty list still reliably means mate."""
    gs = GameState.from_fen('4k3/8/8/8/8/8/4r3/4K3 w - - 0 1')  # Re2+
    evasions = gs.get_valid_moves(for_ai=True, captures_only=True)
    assert set(evasions) == set(gs.get_valid_moves(for_ai=True))
    # Kxe2 is among them, but so are the quiet king steps
    assert any(gs.board[m[2]][m[3]] != EMPTY for m in evasions)
    assert any(gs.board[m[2]][m[3]] == EMPTY for m in evasions)


def test_captures_only_includes_quiet_promotions(custom_gs):
    """Verify a promotion push (no capture involved) still counts as noisy."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[1][0] = 'wP'  # a7, one step from promotion
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'

    gs = custom_gs(empty_board)
    noisy = gs.get_valid_moves(for_ai=True, captures_only=True)
    # All four promotion pieces (types 3-6), and nothing else is noisy here
    assert {m[4] for m in noisy} == {3, 4, 5, 6}
    assert all(m[:4] == (1, 0, 0, 0) for m in noisy)
