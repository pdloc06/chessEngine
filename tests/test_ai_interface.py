"""
Test suite covering the AI-facing engine interface: lightweight move
execution consistency (perft), piece-set/Zobrist synchronization, null
moves, FEN round trips, and AI tuple <-> Move conversions.
"""
import random

from engine.chess_engine import GameState, Move

START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


# --- Helpers ---
def perft(gs: GameState, depth: int) -> int:
    """Count leaf nodes of the legal move tree using the AI make/unmake path."""
    if depth == 0:
        return 1
    total = 0
    for move in gs.get_valid_moves(for_ai=True):
        undo = gs.make_ai_move(move)
        total += perft(gs, depth - 1)
        gs.unmake_ai_move(move, undo)
    return total


def pieces_from_board(gs: GameState) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Rebuild the white/black piece coordinate sets directly from the board."""
    white: set[tuple[int, int]] = set()
    black: set[tuple[int, int]] = set()
    for r in range(8):
        for c in range(8):
            piece = gs.board[r][c]
            if piece != '--':
                (white if piece[0] == 'w' else black).add((r, c))
    return white, black


# --- Perft: end-to-end correctness of generation + make/unmake ---
def test_perft_initial_position(gs):
    """Verify known perft node counts from the initial position."""
    assert perft(gs, 1) == 20
    assert perft(gs, 2) == 400
    assert perft(gs, 3) == 8902
    assert perft(gs, 4) == 197_281


def test_perft_kiwipete():
    """Verify perft counts for Kiwipete, the classic edge-case stress position.

    It exercises castling, pins, en passant, and promotions at once, so it
    catches move generation bugs the quiet initial position misses.
    """
    gs = GameState.from_fen(
        'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1'
    )
    assert perft(gs, 1) == 48
    assert perft(gs, 2) == 2039
    assert perft(gs, 3) == 97_862


def test_king_can_step_beside_blocked_enemy_pawn():
    """Regression: a non-attacking adjacent pawn must still block the ray.

    After 1. e3 d5 2. Ke2 d4 the king may play Kd3: the black pawn on d4
    does not attack d3, and it shields d3 from the queen on d8. A missing
    `break` in _is_square_attacked once let that ray pass through the pawn.
    """
    gs = GameState.from_fen('rnbqkbnr/ppp1pppp/8/8/3p4/4P3/PPPPKPPP/RNBQ1BNR w kq - 0 3')
    king_moves = {
        Move.from_ai_tuple(move, gs.board).get_uci_notation()
        for move in gs.get_valid_moves(for_ai=True)
        if gs.board[move[0]][move[1]][1] == 'K'
    }
    assert king_moves == {'e2d3', 'e2e1', 'e2f3'}


def test_perft_leaves_state_untouched(gs):
    """Verify a full perft run restores board, sets, and Zobrist key exactly."""
    board_before = [row[:] for row in gs.board]
    key_before = gs.zobrist_key

    perft(gs, 3)

    assert gs.board == board_before
    assert gs.zobrist_key == key_before
    assert (gs.white_pieces, gs.black_pieces) == pieces_from_board(gs)


# --- AI make/unmake synchronization (regression for the piece-set bug) ---
def test_ai_move_random_walk_keeps_state_synced(gs):
    """Verify piece sets and Zobrist key stay exact through random AI moves."""
    rng = random.Random(42)
    stack = []

    for _ in range(300):
        moves = gs.get_valid_moves(for_ai=True)
        if not moves:
            break
        move = rng.choice(moves)
        stack.append((move, gs.make_ai_move(move)))

        assert (gs.white_pieces, gs.black_pieces) == pieces_from_board(gs)
        assert gs.zobrist_key == gs.compute_zobrist_key()

    while stack:
        move, undo = stack.pop()
        gs.unmake_ai_move(move, undo)

    assert (gs.white_pieces, gs.black_pieces) == pieces_from_board(gs)
    assert gs.zobrist_key == gs.compute_zobrist_key()
    assert gs.board == GameState().board


def test_ai_castle_move_updates_rook_tracking(custom_gs):
    """Verify make_ai_move/unmake_ai_move relocate the rook in the piece sets."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[7][7] = 'wR'
    empty_board[0][4] = 'bK'
    gs = custom_gs(empty_board)

    castle_tuple = (7, 4, 7, 6, 1)  # O-O
    undo = gs.make_ai_move(castle_tuple)
    assert (gs.white_pieces, gs.black_pieces) == pieces_from_board(gs)
    assert gs.board[7][5] == 'wR' and gs.board[7][6] == 'wK'

    gs.unmake_ai_move(castle_tuple, undo)
    assert (gs.white_pieces, gs.black_pieces) == pieces_from_board(gs)
    assert gs.board[7][7] == 'wR' and gs.board[7][4] == 'wK'


# --- Null moves ---
def test_null_move_round_trip(gs):
    """Verify make_null_move flips only turn/en-passant and reverses exactly."""
    gs.make_move(Move((6, 4), (4, 4), gs.board))  # e4 sets an en-passant square
    key_before = gs.zobrist_key
    ep_before = gs.enpassant_possible
    assert ep_before is not None

    undo = gs.make_null_move()
    assert gs.white_to_move is True  # It was Black's turn; null move passes it
    assert gs.enpassant_possible is None
    assert gs.zobrist_key == gs.compute_zobrist_key()

    gs.unmake_null_move(undo)
    assert gs.white_to_move is False
    assert gs.enpassant_possible == ep_before
    assert gs.zobrist_key == key_before


# --- Zobrist properties ---
def test_zobrist_same_position_same_key(gs):
    """Verify transposing move orders reach the same Zobrist key."""
    other = GameState()

    for move in [((7, 6), (5, 5)), ((0, 6), (2, 5)), ((6, 4), (5, 4))]:
        gs.make_move(Move.normal(move[0], move[1], gs.board))
    for move in [((6, 4), (5, 4)), ((0, 6), (2, 5)), ((7, 6), (5, 5))]:
        other.make_move(Move.normal(move[0], move[1], other.board))

    assert gs.zobrist_key == other.zobrist_key


def test_zobrist_differs_by_side_to_move(custom_gs):
    """Verify identical placements with different side to move hash differently."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    white_turn = custom_gs([row[:] for row in empty_board], white_turn=True)
    black_turn = custom_gs([row[:] for row in empty_board], white_turn=False)

    assert white_turn.zobrist_key != black_turn.zobrist_key


# --- FEN interoperability ---
def test_from_fen_initial_position(gs):
    """Verify parsing the standard start FEN reproduces a fresh GameState."""
    parsed = GameState.from_fen(START_FEN)
    assert parsed.board == gs.board
    assert parsed.white_to_move is True
    assert parsed.zobrist_key == gs.zobrist_key


def test_fen_round_trip():
    """Verify to_fen(from_fen(x)) preserves every FEN field."""
    fen = 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1'
    gs = GameState.from_fen(fen)
    assert gs.to_fen() == fen


def test_from_fen_en_passant_square():
    """Verify the FEN en-passant field maps to the engine's target square."""
    gs = GameState.from_fen('rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq e6 0 2')
    assert gs.enpassant_possible == (2, 4)  # e6


def test_from_fen_castling_rights():
    """Verify partial castling rights parse correctly."""
    gs = GameState.from_fen('r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1')
    assert gs.white_castle_king_side is True
    assert gs.white_castle_queen_side is False
    assert gs.black_castle_king_side is False
    assert gs.black_castle_queen_side is True


# --- Move conversions and UCI notation ---
def test_move_ai_tuple_round_trip(gs):
    """Verify Move -> tuple -> Move survives for every legal opening move."""
    for move in gs.get_valid_moves():
        rebuilt = Move.from_ai_tuple(move.to_ai_tuple(), gs.board)
        assert rebuilt == move


def test_from_ai_tuple_promotion(custom_gs):
    """Verify AI promotion codes map to the right promotion pieces."""
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[1][0] = 'wP'
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    gs = custom_gs(empty_board)

    for code, piece in [(3, 'Q'), (4, 'R'), (5, 'B'), (6, 'N')]:
        move = Move.from_ai_tuple((1, 0, 0, 0, code), gs.board)
        assert move.is_pawn_promotion
        assert move.promotion_piece == piece


def test_uci_notation(gs):
    """Verify UCI coordinate output, including the promotion suffix."""
    assert Move.normal((6, 4), (4, 4), gs.board).get_uci_notation() == 'e2e4'

    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[1][0] = 'wP'
    promo = Move.promotion((1, 0), (0, 0), empty_board, promotion_piece='N')
    assert promo.get_uci_notation() == 'a7a8n'
