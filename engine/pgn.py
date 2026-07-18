"""
PGN and SAN import utilities for the game-review feature.

A PGN (Portable Game Notation) file has two parts: a header of `[Tag "Value"]`
pairs, and the movetext — the moves in SAN (Standard Algebraic Notation, the
human-readable "Nf3"/"exd5" format), possibly decorated with move numbers,
`{comments}`, `(variations)`, `$n` evaluation glyphs, and a game result.

This module strips all the decoration down to the mainline SAN tokens and
resolves each token against the engine's own legal-move generator: instead of
re-implementing chess rules, a SAN string is simply matched against the Move
objects `GameState.get_valid_moves()` already produces. That guarantees any
move we accept is legal, and any ambiguity in the PGN is detected for free.

Like the rest of `engine/`, this module is pure stdlib.
"""
import re

from engine.chess_engine import GameState, INT_TO_CODE, Move

# One `[Tag "Value"]` header pair, e.g. [Event "Casual game"]
_TAG_RE = re.compile(r'^\s*\[(\w+)\s+"(.*)"\]\s*$')
# `{...}` comments may span lines, hence DOTALL
_COMMENT_RE = re.compile(r'\{.*?\}', re.DOTALL)
# Numeric Annotation Glyphs like $14
_NAG_RE = re.compile(r'\$\d+')
# Move numbers: "1." for White, "1..." when Black's move follows a comment
_MOVE_NUMBER_RE = re.compile(r'\d+\.(?:\.\.)?')
# Game-result markers that terminate the movetext
_RESULTS = frozenset({'1-0', '0-1', '1/2-1/2', '*'})

# The shape of one (non-castling) SAN token, after check/annotation suffixes
# are stripped: optional piece letter, optional disambiguation file/rank,
# optional capture 'x', destination square, optional promotion piece.
_SAN_RE = re.compile(r'^([KQRBN])?([a-h])?([1-8])?(x)?([a-h][1-8])(?:=?([QRBN]))?$')


class PgnError(ValueError):
    """Raised when a PGN or SAN string cannot be resolved into legal moves."""


def looks_like_fen(text: str) -> bool:
    """
    Guess whether an imported string is a FEN rather than a PGN.

    A FEN is a single line whose first field packs the eight board ranks
    separated by seven '/' characters — nothing in PGN movetext looks like
    that, so the check is a reliable discriminator for the import screen.

    Parameters
    ----------
    text : str
        The raw user-supplied string.

    Returns
    -------
    bool
        True when the text should be parsed as a FEN.
    """
    stripped = text.strip()
    if '\n' in stripped:
        return False
    fields = stripped.split()
    return len(fields) >= 4 and fields[0].count('/') == 7


def parse_pgn(text: str) -> tuple[str | None, list[str]]:
    """
    Split a PGN string into its optional FEN header and mainline SAN tokens.

    Parameters
    ----------
    text : str
        The full PGN text (headers optional; bare movetext is accepted).

    Returns
    -------
    tuple of (str or None, list of str)
        The starting FEN from a `[FEN "..."]` header (None means the standard
        start position) and the SAN move tokens of the mainline, in order.
    """
    headers: dict[str, str] = {}
    movetext_lines: list[str] = []
    for line in text.splitlines():
        tag = _TAG_RE.match(line)
        if tag:
            headers[tag.group(1)] = tag.group(2)
        elif not line.lstrip().startswith('%'):  # '%' lines are PGN escapes
            movetext_lines.append(line)

    movetext = ' '.join(movetext_lines)
    movetext = _COMMENT_RE.sub(' ', movetext)
    movetext = _strip_variations(movetext)
    movetext = _NAG_RE.sub(' ', movetext)
    movetext = _MOVE_NUMBER_RE.sub(' ', movetext)

    tokens = [
        token for token in movetext.split()
        if token not in _RESULTS and token.strip('.')  # drop stray ellipses
    ]
    return headers.get('FEN'), tokens


def _strip_variations(text: str) -> str:
    """Remove `(...)` variation blocks, which may nest, from movetext."""
    kept: list[str] = []
    depth = 0
    for char in text:
        if char == '(':
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            kept.append(char)
    return ''.join(kept)


def san_to_move(gs: GameState, san: str, valid_moves: list[Move] | None = None) -> Move:
    """
    Resolve one SAN token against the legal moves of the current position.

    Parameters
    ----------
    gs : GameState
        The position the move is played in.
    san : str
        The SAN token, e.g. 'Nf3', 'exd5', 'O-O', 'e8=Q+', 'Rad1'.
    valid_moves : list of Move, optional
        Pre-generated legal moves for the position (avoids regenerating them
        when the caller already has the list).

    Returns
    -------
    Move
        The unique legal move the token describes.

    Raises
    ------
    PgnError
        If the token is unreadable, matches no legal move, or is ambiguous.
    """
    if valid_moves is None:
        valid_moves = gs.get_valid_moves()

    # Trailing check/mate marks and annotation glyphs carry no move identity
    token = san.strip()
    while token and token[-1] in '+#!?':
        token = token[:-1]

    if token in ('O-O', '0-0'):
        matches = [m for m in valid_moves if m.is_castle_move and m.end_col > m.start_col]
    elif token in ('O-O-O', '0-0-0'):
        matches = [m for m in valid_moves if m.is_castle_move and m.end_col < m.start_col]
    else:
        parsed = _SAN_RE.match(token)
        if parsed is None:
            raise PgnError(f'Unreadable move: {san!r}')
        piece, file_hint, rank_hint, _, dest, promo = parsed.groups()
        piece = piece or 'P'
        end_row = Move.RANKS_TO_ROWS[dest[1]]
        end_col = Move.FILES_TO_COLS[dest[0]]

        matches = []
        for move in valid_moves:
            if move.is_castle_move or INT_TO_CODE[move.piece_moved][1] != piece:
                continue
            if (move.end_row, move.end_col) != (end_row, end_col):
                continue
            if file_hint and Move.COLS_TO_FILES[move.start_col] != file_hint:
                continue
            if rank_hint and Move.ROWS_TO_RANKS[move.start_row] != rank_hint:
                continue
            # Promotions generate one Move per piece choice, so the promotion
            # letter is part of the move's identity, not an afterthought
            if move.is_pawn_promotion != bool(promo):
                continue
            if promo and move.promotion_piece != promo:
                continue
            matches.append(move)

    if not matches:
        raise PgnError(f'Illegal move in this position: {san!r}')
    if len(matches) > 1:
        raise PgnError(f'Ambiguous move: {san!r}')
    return matches[0]


def game_from_pgn(text: str) -> GameState:
    """
    Replay a PGN's mainline into a fresh GameState.

    The returned state has every move applied through `make_move()`, so its
    `move_log` carries full notation metadata (disambiguation, check marks)
    exactly as if the game had been played through the UI.

    Parameters
    ----------
    text : str
        The full PGN text.

    Returns
    -------
    GameState
        The state after the final mainline move, with a complete move log.

    Raises
    ------
    PgnError
        If any movetext token cannot be resolved to a legal move.
    ValueError
        If a `[FEN "..."]` header holds an invalid FEN.
    """
    start_fen, tokens = parse_pgn(text)
    gs = GameState.from_fen(start_fen) if start_fen else GameState()
    if not tokens and start_fen is None:
        raise PgnError('No moves found in PGN')

    for token in tokens:
        move = san_to_move(gs, token, gs.get_valid_moves())
        gs.make_move(move)

    # Refresh terminal flags and stamp '#' on a mating final move, mirroring
    # what the game loop does after each move
    gs.get_valid_moves()
    if gs.is_checkmate and gs.move_log:
        gs.move_log[-1].is_checkmate = True
        gs.move_log[-1].is_check = False
    return gs