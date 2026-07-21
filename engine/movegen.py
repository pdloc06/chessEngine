"""
Move generation: turning a position into the list of moves that are legal in it.

Two phases, unchanged from when this lived inside `GameState`:

1. Generate pseudo-legal moves for each piece.
2. Filter them through pin and check logic so only legal moves survive.

These are free functions taking a `GameState` rather than methods on it, which
is what keeps the split one-way — `engine.board` never imports this module.
Attack detection stayed behind in `board`, because pins and checks are
properties of a position and `make_move` needs them to annotate check for SAN.

There are two shapes of output, and the caller picks with `for_ai`:

- `for_ai=False` returns `Move` objects, for the UI, SAN and the move log.
- `for_ai=True` returns 5-tuples `(start_row, start_col, end_row, end_col,
  move_type)`, which is what the search expands. It also skips threefold
  hashing, because the search tracks repetition itself via Zobrist keys.
"""
from engine.board import (
    AI_PROMO_CODES, ALL_DIRECTIONS, BB, BISHOP, BK, BN, BP, BQ, BR,
    DIAGONAL_DIRECTIONS, EMPTY, GameState, KING, KNIGHT, KNIGHT_DELTAS, Move,
    ORTHOGONAL_DIRECTIONS, PAWN, PIECE_TYPE, QUEEN, ROOK,
    WB, WK, WN, WP, WQ, WR,
)

# Type alias for the lightweight move format the search expands
MoveTuple = tuple[int, int, int, int, int]


def generate_legal(gs: GameState, for_ai: bool = False, captures_only: bool = False) -> list:
    """
    Generate all legal moves in the current position.

    Calculates checks and pins to filter pseudo-legal moves, ensuring
    no move leaves the king in check. Returns either Move objects for UI
    or lightweight tuples for AI processing.

    Parameters
    ----------
    for_ai : bool, optional
        Flag to toggle output format. If False, returns Move objects with
        notation metadata and draw-rule enforcement. If True, returns
        lightweight 5-element tuples and skips draw-rule hashing entirely,
        because the search layer handles repetition via Zobrist keys.
    captures_only : bool, optional
        If True, generate only "noisy" moves — captures, en passant, and
        promotions — without ever materializing quiet moves. This is the
        quiescence-search fast path: most
        search nodes are quiescence nodes, and skipping quiet moves there
        saves both the generation and the later filtering work. Ignored
        while in check, where the complete evasion list is returned so an
        empty result still reliably means checkmate.

    Returns
    -------
    list of Move or list of tuple of int
        The legal moves available to the side to move.
    """
    moves = []
    gs.in_check, gs.pins, gs.checks = gs.check_pins_checks()
    king_row, king_col = (
        gs.white_king_location if gs.white_to_move else gs.black_king_location
    )

    if gs.in_check:
        captures_only = False  # in check the caller needs every evasion (see docstring)
        if len(gs.checks) == 1:  # Single check -> Block, capture, or evade
            moves = _all_pseudo_legal_moves(gs, for_ai=for_ai)
            check = gs.checks[0]
            check_row, check_col = check[0], check[1]
            piece_checking = gs.board[check_row][check_col]
            valid_squares = set()

            # If checking piece is a knight, block is impossible; must capture or move king
            if PIECE_TYPE[piece_checking] == KNIGHT:
                valid_squares = {(check_row, check_col)}
            else:
                for i in range(1, 8):
                    valid_square = (king_row + check[2] * i, king_col + check[3] * i)
                    valid_squares.add(valid_square)
                    if valid_square == (check_row, check_col):
                        break

            # Remove moves that don't block the check, capture the checker, or move the king
            for i in range(len(moves) - 1, -1, -1):
                if for_ai:
                    start_row, start_col, end_row, end_col, _ = moves[i]
                    if PIECE_TYPE[gs.board[start_row][start_col]] != KING:
                        if (end_row, end_col) not in valid_squares:
                            del moves[i]
                else:
                    if PIECE_TYPE[moves[i].piece_moved] != KING:
                        if (moves[i].end_row, moves[i].end_col) not in valid_squares:
                            del moves[i]
        else:  # Double check -> King is strictly forced to move
            _king_moves(gs, king_row, king_col, moves, for_ai=for_ai)
    else:
        moves = _all_pseudo_legal_moves(gs, for_ai=for_ai, captures_only=captures_only)

    # Filter out invalid En Passant moves that expose a horizontal pin.
    # This runs before mate/stalemate evaluation so an illegal en passant
    # can never masquerade as the "only" escape move.
    for i in range(len(moves) - 1, -1, -1):
        if for_ai:
            if moves[i][4] == 2:  # En Passant move type index
                undo_package = gs.make_ai_move(moves[i])
                # make_ai_move switches the turn to the enemy. We must switch it back
                # momentarily to check if our own king is in check.
                gs.white_to_move = not gs.white_to_move
                in_check, _, _ = gs.check_pins_checks()
                # Revert the turn back before unmaking the move entirely
                gs.white_to_move = not gs.white_to_move
                gs.unmake_ai_move(moves[i], undo_package)
                if in_check:
                    del moves[i]
        else:
            if moves[i].move_type == Move.EN_PASSANT:
                gs.make_move(moves[i], annotate=False)
                gs.white_to_move = not gs.white_to_move
                in_check, _, _ = gs.check_pins_checks()
                gs.white_to_move = not gs.white_to_move
                gs.unmake_move()
                if in_check:
                    del moves[i]

    # Evaluate Checkmate or Stalemate statuses. Checkmate stays reliable
    # under captures_only (in check the full evasion list was generated),
    # but an empty captures-only list says nothing about stalemate — the
    # quiet moves were simply never generated — so the flags are left
    # untouched in that case.
    if len(moves) == 0:
        if gs.in_check:
            gs.is_checkmate = True
        elif not captures_only:
            gs.is_stalemate = True
    else:
        gs.is_checkmate = False
        gs.is_stalemate = False

        # Draw conditions (50-move limit or 3-fold repetition).
        # Skipped for AI: make_ai_move doesn't maintain these logs, and
        # hashing the whole board at every node would dominate search time.
        if not for_ai:
            current_state = gs.get_board_state()
            if gs.halfmove_clock >= 100 or gs.state_counts.get(current_state, 0) >= 3:
                gs.is_stalemate = True
                moves = []

    # Calculate Ambiguous Notation mapping for UI
    if not for_ai and len(moves) > 0:
        move_map: dict[tuple, list[Move]] = {}
        for move in moves:
            if PIECE_TYPE[move.piece_moved] != PAWN:
                key = (move.piece_moved, move.end_row, move.end_col)
                if key not in move_map:
                    move_map[key] = []
                move_map[key].append(move)

        for key, matching_moves in move_map.items():
            if len(matching_moves) > 1:
                for move in matching_moves:
                    cols = [m.start_col for m in matching_moves]
                    if cols.count(move.start_col) == 1:
                        move.disambiguation = Move.COLS_TO_FILES[move.start_col]
                    else:
                        rows = [m.start_row for m in matching_moves]
                        if rows.count(move.start_row) == 1:
                            move.disambiguation = Move.ROWS_TO_RANKS[move.start_row]
                        else:
                            move.disambiguation = (
                                Move.COLS_TO_FILES[move.start_col] + Move.ROWS_TO_RANKS[move.start_row]
                            )

    return moves

def _all_pseudo_legal_moves(gs: GameState, for_ai: bool = False, captures_only: bool = False) -> list:
    """Scan active pieces and fetch logic bounds for pseudo-legal moves."""
    possible_moves: list = []
    active_pieces: set[tuple[int, int]] = gs.white_pieces if gs.white_to_move else gs.black_pieces
    board = gs.board

    for row, col in active_pieces:
        piece_type = PIECE_TYPE[board[row][col]]
        _MOVE_FUNCTIONS[piece_type](gs, row, col, possible_moves, for_ai, captures_only)
        if piece_type == KING and not captures_only:  # castling is never a capture
            _castle_moves(gs, row, col, possible_moves, for_ai)
    return possible_moves


def _pawn_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal moves for a pawn at the specified location."""
    move_amount = -1 if gs.white_to_move else 1
    start_row = 6 if gs.white_to_move else 1
    back_row = 0 if gs.white_to_move else 7
    _is_back_row = row + move_amount == back_row

    piece_pinned = False
    pin_direction: tuple[int, int] | tuple[()] = ()
    if (row, col) in gs.pins:
        piece_pinned = True
        pin_direction = gs.pins[(row, col)]

    def _add_move(end_row: int, end_col: int) -> None:
        if _is_back_row:
            if for_ai:
                # Append 4 promotion tuple states: 3=Q, 4=R, 5=B, 6=N
                for m_type in (3, 4, 5, 6):
                    possible_moves.append((row, col, end_row, end_col, m_type))
            else:
                for piece in ['Q', 'R', 'B', 'N']:
                    possible_moves.append(
                        Move.promotion(
                            (row, col), (end_row, end_col),
                            gs.board, promotion_piece=piece
                        )
                    )
        else:
            if for_ai:
                possible_moves.append((row, col, end_row, end_col, 0))
            else:
                possible_moves.append(
                    Move.normal((row, col), (end_row, end_col), gs.board)
                )

    # Pushes are quiet moves — except a push to the back row, which is a
    # promotion and therefore "noisy" even for a captures-only caller
    if gs.board[row + move_amount][col] == EMPTY and (not captures_only or _is_back_row):
        if not piece_pinned or pin_direction == (-1, 0) or pin_direction == (1, 0):
            _add_move(row + move_amount, col)
            if row == start_row and gs.board[row + 2 * move_amount][col] == EMPTY:
                if for_ai:
                    possible_moves.append((row, col, row + 2 * move_amount, col, 0))
                else:
                    possible_moves.append(
                        Move.normal((row, col), (row + 2 * move_amount, col), gs.board)
                    )

    for col_offset in (-1, 1):
        new_col = col + col_offset
        if 0 <= new_col < 8:
            if not piece_pinned or pin_direction == (move_amount, col_offset):
                end_piece = gs.board[row + move_amount][new_col]
                enemy_lo, enemy_hi = (BP, BK) if gs.white_to_move else (WP, WK)
                if enemy_lo <= end_piece <= enemy_hi:
                    _add_move(row + move_amount, new_col)

                if (row + move_amount, new_col) == gs.enpassant_possible:
                    if for_ai:
                        possible_moves.append((row, col, row + move_amount, new_col, 2))
                    else:
                        possible_moves.append(
                            Move.en_passant((row, col), (row + move_amount, new_col), gs.board)
                        )

def _rook_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal moves for a rook at the specified location."""
    _sliding_moves(gs, row, col, possible_moves, ORTHOGONAL_DIRECTIONS, for_ai, captures_only)

def _bishop_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal moves for a bishop at the specified location."""
    _sliding_moves(gs, row, col, possible_moves, DIAGONAL_DIRECTIONS, for_ai, captures_only)

def _queen_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal moves for a queen at the specified location."""
    _rook_moves(gs, row, col, possible_moves, for_ai, captures_only)
    _bishop_moves(gs, row, col, possible_moves, for_ai, captures_only)

def _sliding_moves(
        gs: GameState,
        row: int,
        col: int,
        possible_moves: list,
        directions: tuple[tuple[int, int], ...],
        for_ai: bool = False,
        captures_only: bool = False
) -> None:
    """Helper method to iterate ray directions for sliding pieces (Rook, Bishop, Queen)."""
    piece_pinned = False
    pin_direction: tuple[int, int] | tuple[()] = ()
    if (row, col) in gs.pins:
        piece_pinned = True
        pin_direction = gs.pins[(row, col)]

    board = gs.board
    enemy_lo, enemy_hi = (BP, BK) if gs.white_to_move else (WP, WK)

    for d in directions:
        # Skip whole rays early when pinned off-axis (saves the inner loop)
        if piece_pinned and pin_direction != (d[0], d[1]) and pin_direction != (-d[0], -d[1]):
            continue

        end_row = row
        end_col = col
        while True:
            end_row += d[0]
            end_col += d[1]
            if 0 <= end_row < 8 and 0 <= end_col < 8:
                end_piece = board[end_row][end_col]
                if end_piece == EMPTY:
                    # Quiet slide: skip the append for captures-only
                    # callers but keep walking the ray toward a capture
                    if not captures_only:
                        if for_ai:
                            possible_moves.append((row, col, end_row, end_col, 0))
                        else:
                            possible_moves.append(Move.normal((row, col), (end_row, end_col), board))
                elif enemy_lo <= end_piece <= enemy_hi:
                    if for_ai:
                        possible_moves.append((row, col, end_row, end_col, 0))
                    else:
                        possible_moves.append(Move.normal((row, col), (end_row, end_col), board))
                    break
                else:
                    break
            else:
                break

def _knight_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal moves for a knight at the specified location."""
    # A pinned knight can never move: it cannot stay on the pin ray
    if (row, col) in gs.pins:
        return

    board = gs.board
    enemy_lo, enemy_hi = (BP, BK) if gs.white_to_move else (WP, WK)

    for move in KNIGHT_DELTAS:
        end_row = row + move[0]
        end_col = col + move[1]
        if 0 <= end_row < 8 and 0 <= end_col < 8:
            end_piece = board[end_row][end_col]
            if (enemy_lo <= end_piece <= enemy_hi) or (end_piece == EMPTY and not captures_only):
                if for_ai:
                    possible_moves.append((row, col, end_row, end_col, 0))
                else:
                    possible_moves.append(Move.normal((row, col), (end_row, end_col), board))

def _king_moves(
        gs: GameState, row: int, col: int, possible_moves: list,
        for_ai: bool = False, captures_only: bool = False
) -> None:
    """Get all pseudo-legal normal moves for a king validating safe surrounding squares."""
    board = gs.board
    enemy_lo, enemy_hi = (BP, BK) if gs.white_to_move else (WP, WK)

    for d in ALL_DIRECTIONS:
        end_row = row + d[0]
        end_col = col + d[1]
        if 0 <= end_row < 8 and 0 <= end_col < 8:
            end_piece = board[end_row][end_col]
            # The captures-only test runs before the attack scan: skipping
            # quiet squares early also skips their is_square_attacked cost
            if (enemy_lo <= end_piece <= enemy_hi) or (end_piece == EMPTY and not captures_only):
                if not gs.is_square_attacked(end_row, end_col):
                    if for_ai:
                        possible_moves.append((row, col, end_row, end_col, 0))
                    else:
                        possible_moves.append(Move.normal((row, col), (end_row, end_col), board))

def _castle_moves(gs: GameState, row: int, col: int, possible_moves: list, for_ai: bool = False) -> None:
    """
    Identify available castling moves bound by legal logic and piece configurations.

    White and Black castling differ only by home rank and piece color, so
    both sides share one implementation: each wing needs its castling
    right intact, the squares between king and rook empty, the rook still
    home, and the king's path (its square plus the two it crosses) safe
    from attack.
    """
    if gs.white_to_move:
        home, rook = 7, WR
        home_square = gs.WHITE_KING_HOME_SQUARE
        king_side, queen_side = gs.white_castle_king_side, gs.white_castle_queen_side
    else:
        home, rook = 0, BR
        home_square = gs.BLACK_KING_HOME_SQUARE
        king_side, queen_side = gs.black_castle_king_side, gs.black_castle_queen_side

    if (row, col) != home_square:
        return
    board = gs.board

    if (
        king_side
        and board[home][5] == EMPTY and board[home][6] == EMPTY
        and board[home][7] == rook
        and gs.squares_safe_for_castle([(home, 4), (home, 5), (home, 6)])
    ):
        if for_ai:
            possible_moves.append((home, 4, home, 6, 1))
        else:
            possible_moves.append(Move.castle((home, 4), (home, 6), board))

    if (
        queen_side
        and board[home][1] == EMPTY and board[home][2] == EMPTY and board[home][3] == EMPTY
        and board[home][0] == rook
        and gs.squares_safe_for_castle([(home, 4), (home, 3), (home, 2)])
    ):
        if for_ai:
            possible_moves.append((home, 4, home, 2, 1))
        else:
            possible_moves.append(Move.castle((home, 4), (home, 2), board))


def generate_captures(gs: GameState, for_ai: bool = True) -> list:
    """
    Generate only the legal captures in a position, for quiescence search.

    Parameters
    ----------
    gs : GameState
        Position to generate for.
    for_ai : bool, optional
        Return 5-tuples rather than `Move` objects. Defaults to True, since
        quiescence is the only caller that matters.

    Returns
    -------
    list
        Legal captures only. An empty list here says nothing about checkmate
        or stalemate -- ask `generate_legal` for that.
    """
    return generate_legal(gs, for_ai=for_ai, captures_only=True)


# Dispatch keyed by the 1-6 piece-type index (see PIECE_TYPE). A module
# constant rather than the dict of bound methods `GameState.__init__` used to
# rebuild for every position; the dispatch itself is unchanged.
_MOVE_FUNCTIONS = {
    PAWN: _pawn_moves,
    ROOK: _rook_moves,
    BISHOP: _bishop_moves,
    KNIGHT: _knight_moves,
    QUEEN: _queen_moves,
    KING: _king_moves,
}
