"""
Test suite covering piece movement logic, move validity,
and special mechanics (En Passant, Castling, Promotion).
"""
from chess_engine import Move


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