"""
Test suite covering internal engine mechanics: Piece tracking synchronization,
Ambiguous Notation (PGN) resolution, and AI performance flags.
"""
from engine.chess_engine import GameState, Move, EMPTY, WN, WQ, BK, BP


# --- Helpers specific to internal mechanics ---
def get_actual_pieces(gs: GameState) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Helper to count pieces directly from the 2D matrix as sets."""
    white, black = set(), set()
    for r in range(8):
        for c in range(8):
            piece = gs.board[r][c]
            if piece != EMPTY:
                if piece < BP:
                    white.add((r, c))
                else:
                    black.add((r, c))
    return white, black


def find_move(valid_moves: list, sr: int, sc: int, er: int, ec: int):
    """
    Helper to locate a specific move within the valid moves list.
    Supports both standard Move objects and lightweight AI tuples.

    Parameters
    ----------
    valid_moves : list
        The list of valid moves (can be Move objects or tuples).
    sr : int
        Start row of the move to find.
    sc : int
        Start col of the move to find.
    er : int
        End row of the move to find.
    ec : int
        End col of the move to find.

    Returns
    -------
    Move or tuple or None
        The found move, or None if the move is not in the list.
    """
    for move in valid_moves:
        # Check if the move is a lightweight AI tuple
        if isinstance(move, tuple):
            if move[0] == sr and move[1] == sc and move[2] == er and move[3] == ec:
                return move
        # Otherwise, treat it as a standard UI Move object
        else:
            if (move.start_row == sr and move.start_col == sc and
                move.end_row == er and move.end_col == ec):
                return move
    return None


# --- Internal Tracking Tests ---
def test_piece_tracking_sync_normal(gs):
    """Verify tracking sets update accurately on standard moves and undos."""
    gs.make_move(Move((6, 4), (4, 4), gs.board))
    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab

    gs.unmake_move()
    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab


def test_piece_tracking_sync_en_passant(gs):
    """Verify tracking sets synchronize properly during En Passant scenarios."""
    gs.make_move(Move((6, 4), (4, 4), gs.board))
    gs.make_move(Move((1, 0), (2, 0), gs.board))
    gs.make_move(Move((4, 4), (3, 4), gs.board))
    gs.make_move(Move((1, 3), (3, 3), gs.board))

    ep_move = Move((3, 4), (2, 3), gs.board, move_type=Move.EN_PASSANT)
    gs.make_move(ep_move)

    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab

    gs.unmake_move()
    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab


def test_piece_tracking_sync_castling(gs):
    """Verify tracking sets synchronize properly during Castling."""
    gs.board[7][5] = EMPTY
    gs.board[7][6] = EMPTY
    gs.white_pieces.discard((7, 5))
    gs.white_pieces.discard((7, 6))

    castle_move = Move((7, 4), (7, 6), gs.board, move_type=Move.CASTLE)
    gs.make_move(castle_move)

    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab

    gs.unmake_move()
    aw, ab = get_actual_pieces(gs)
    assert gs.white_pieces == aw and gs.black_pieces == ab


# --- Notation & AI Tests ---
def test_ambiguous_notation_different_file(empty_kings_gs):
    """Notation: Differentiate identical pieces attacking the same square by file."""
    gs = empty_kings_gs
    gs.board[6][3] = WN;
    gs.white_pieces.add((6, 3))
    gs.board[7][6] = WN;
    gs.white_pieces.add((7, 6))

    valid_moves = gs.get_valid_moves(for_ai=False)
    move_d2 = find_move(valid_moves, 6, 3, 5, 5)
    move_g1 = find_move(valid_moves, 7, 6, 5, 5)

    assert move_d2.get_chess_notation() == 'Ndf3'
    assert move_g1.get_chess_notation() == 'Ngf3'


def test_ambiguous_notation_same_file(empty_kings_gs):
    """Notation: Differentiate identical pieces on the same file by rank."""
    gs = empty_kings_gs
    gs.board[7][5] = WN;
    gs.white_pieces.add((7, 5))
    gs.board[3][5] = WN;
    gs.white_pieces.add((3, 5))

    valid_moves = gs.get_valid_moves(for_ai=False)
    move_f1 = find_move(valid_moves, 7, 5, 5, 4)
    move_f5 = find_move(valid_moves, 3, 5, 5, 4)

    assert move_f1.get_chess_notation() == 'N1e3'
    assert move_f5.get_chess_notation() == 'N5e3'


def test_ambiguous_notation_file_and_rank_overlap(empty_kings_gs):
    """Notation: Differentiate using both file and rank in complex overlaps."""
    gs = empty_kings_gs
    gs.board[6][3] = WQ;
    gs.white_pieces.add((6, 3))
    gs.board[0][3] = WQ;
    gs.white_pieces.add((0, 3))
    gs.board[6][0] = WQ;
    gs.white_pieces.add((6, 0))

    valid_moves = gs.get_valid_moves(for_ai=False)
    move_d2 = find_move(valid_moves, 6, 3, 3, 3)
    move_d8 = find_move(valid_moves, 0, 3, 3, 3)
    move_a2 = find_move(valid_moves, 6, 0, 3, 3)

    assert move_d2.get_chess_notation() == 'Qd2d5'
    assert move_d8.get_chess_notation() == 'Q8d5'
    assert move_a2.get_chess_notation() == 'Qad5'


def test_ambiguous_notation_with_capture_and_check(empty_kings_gs):
    """Notation: Ensure disambiguation meshes properly with capture/check markers."""
    gs = empty_kings_gs
    gs.board[0][4] = EMPTY
    gs.black_pieces.remove((0, 4))

    # FIX: Place Black King on e5 (row 3, col 4) instead of f5.
    # A Knight on f3 (row 5, col 5) will validly check a King on e5.
    gs.board[3][4] = BK
    gs.black_pieces.add((3, 4))
    gs.black_king_location = (3, 4)

    gs.board[6][3] = WN
    gs.white_pieces.add((6, 3))
    gs.board[7][6] = WN
    gs.white_pieces.add((7, 6))
    gs.board[5][5] = BP
    gs.black_pieces.add((5, 5))

    valid_moves = gs.get_valid_moves(for_ai=False)
    move_d2 = find_move(valid_moves, 6, 3, 5, 5)

    # Execute the move so the engine calculates the check state (annotate=True)
    gs.make_move(move_d2)

    assert move_d2.get_chess_notation() == 'Ndxf3+'


def test_for_ai_flag_bypasses_notation(empty_kings_gs):
    """AI Optimizer: Ensure the for_ai flag skips expensive string allocations."""
    gs = empty_kings_gs
    gs.board[6][3] = WN;
    gs.white_pieces.add((6, 3))
    gs.board[7][6] = WN;
    gs.white_pieces.add((7, 6))

    valid_moves = gs.get_valid_moves(for_ai=True)
    move_d2 = find_move(valid_moves, 6, 3, 5, 5)
    assert getattr(move_d2, 'disambiguation', '') == ''