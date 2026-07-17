"""
Test suite covering game states: Absolute Pins, Checks, Checkmates,
Stalemates, and Draw Mechanics (50-move rule, Threefold Repetition).
"""
from engine.chess_engine import Move, WN, WR, BN


def test_absolute_pin(custom_gs):
    """Verify pieces pinned to the King cannot move."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'  # King e1
    empty_board[6][4] = 'wN'  # Knight e2
    empty_board[0][4] = 'bR'  # Rook e8
    empty_board[0][0] = 'bK'

    gs = custom_gs(empty_board)
    valid_moves = gs.get_valid_moves()

    knight_moves = [m for m in valid_moves if m.piece_moved == WN]
    assert len(knight_moves) == 0


def test_fools_mate(gs):
    """Verify fastest possible checkmate sets correct flags."""
    gs.make_move(Move((6, 5), (5, 5), gs.board))  # f3
    gs.make_move(Move((1, 4), (3, 4), gs.board))  # e5
    gs.make_move(Move((6, 6), (4, 6), gs.board))  # g4
    gs.make_move(Move((0, 3), (4, 7), gs.board))  # Qh4#

    valid_moves = gs.get_valid_moves()
    assert len(valid_moves) == 0
    assert gs.is_checkmate is True
    assert gs.is_stalemate is False


def test_stalemate_edge_case(custom_gs):
    """Verify stalemate flag is set when no legal moves exist but King is not in check."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[0][7] = 'bK'  # h8
    empty_board[1][5] = 'wK'  # f7
    empty_board[2][6] = 'wQ'  # g6

    gs = custom_gs(empty_board, white_turn=False)
    valid_moves = gs.get_valid_moves()

    assert len(valid_moves) == 0
    assert gs.is_checkmate is False
    assert gs.is_stalemate is True


def test_promotion_to_block_check(custom_gs):
    """Verify promoting a pawn to block or capture a checking piece is legal."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[6][0] = 'wK'  # a2
    empty_board[1][1] = 'wP'  # b7
    empty_board[0][0] = 'bR'  # Rook a8 checks White King
    empty_board[0][7] = 'bK'

    gs = custom_gs(empty_board)
    valid_moves = gs.get_valid_moves()
    promotion_capture = Move.promotion((1, 1), (0, 0), gs.board)
    assert promotion_capture in valid_moves


def test_50_move_rule_triggers_draw(gs):
    """Verify reaching 100 half-moves without pawn moves or captures triggers a draw."""
    gs.halfmove_clock = 99
    gs.make_move(Move((7, 6), (5, 5), gs.board))  # Nf3
    valid_moves = gs.get_valid_moves()

    assert gs.halfmove_clock == 100
    assert gs.is_stalemate is True
    assert len(valid_moves) == 0


def test_50_move_rule_resets_on_pawn_move(gs):
    """Verify the 50-move clock resets to 0 upon a pawn move."""
    gs.halfmove_clock = 99
    gs.make_move(Move((6, 4), (4, 4), gs.board))  # e4
    assert gs.halfmove_clock == 0
    assert gs.is_stalemate is False


def test_50_move_rule_resets_on_capture(empty_kings_gs):
    """Verify the 50-move clock resets to 0 upon any capture."""
    gs = empty_kings_gs
    gs.halfmove_clock = 99
    gs.board[4][4] = WR
    gs.board[4][5] = BN
    gs.white_pieces.add((4, 4))
    gs.black_pieces.add((4, 5))

    gs.make_move(Move((4, 4), (4, 5), gs.board))  # Rxe5
    assert gs.halfmove_clock == 0
    assert gs.is_stalemate is False


def test_threefold_repetition(gs):
    """Verify oscillating moves that repeat the board state 3 times trigger a draw."""
    # FIX: Instantiate the Move object immediately before executing it to ensure
    # it captures the most up-to-date board state and piece references.

    # 1st Repetition (Count = 2)
    m1 = Move.normal((7, 6), (5, 5), gs.board)
    gs.make_move(m1)

    m2 = Move.normal((0, 6), (2, 5), gs.board)
    gs.make_move(m2)

    m3 = Move.normal((5, 5), (7, 6), gs.board)
    gs.make_move(m3)

    m4 = Move.normal((2, 5), (0, 6), gs.board)
    gs.make_move(m4)

    # 2nd Repetition (Count = 3)
    m1_2 = Move.normal((7, 6), (5, 5), gs.board)
    gs.make_move(m1_2)

    m2_2 = Move.normal((0, 6), (2, 5), gs.board)
    gs.make_move(m2_2)

    m3_2 = Move.normal((5, 5), (7, 6), gs.board)
    gs.make_move(m3_2)

    m4_2 = Move.normal((2, 5), (0, 6), gs.board)
    gs.make_move(m4_2)

    valid_moves = gs.get_valid_moves()
    assert gs.is_stalemate is True
    assert len(valid_moves) == 0


def test_threefold_repetition_invalidated_by_castling_loss(custom_gs):
    """Verify state repetition requires castling rights to be identical."""
    # FIX: Use custom_gs to clear blocking pawns, preventing the King from
    # illegally stepping on its own pieces during the test sequence.
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'  # e1
    empty_board[7][0] = 'wR'  # a1
    empty_board[0][4] = 'bK'  # e8

    gs = custom_gs(empty_board)
    gs.white_castle_queen_side = True  # Grant castling rights

    initial_hash = gs.get_board_state()

    # Sequence altering King castling rights permanently
    m1 = Move.normal((7, 4), (6, 4), gs.board)
    gs.make_move(m1)  # White: Ke2

    m2 = Move.normal((0, 4), (1, 4), gs.board)
    gs.make_move(m2)  # Black: Ke7

    m3 = Move.normal((6, 4), (7, 4), gs.board)
    gs.make_move(m3)  # White: Ke1

    m4 = Move.normal((1, 4), (0, 4), gs.board)
    gs.make_move(m4)  # Black: Ke8

    current_hash = gs.get_board_state()

    assert initial_hash != current_hash
    assert gs.state_counts.get(initial_hash, 0) == 1