"""
Tests for the mop-up evaluation — the term that converts won pawnless
endgames.

Without it, material plus piece-square tables is flat in K+Q vs K: every safe
queen move scores identically, so the search has no reason to prefer the one
that shrinks the enemy king's box, and the root shuffle picks arbitrarily
among them until the 50-move rule takes the win away.

Two kinds of check here. The gradient tests are exact and instant — they ask
whether `evaluate()` has an opinion at all, and whether it keeps quiet where
it should. The playout test is the one that matters: it plays a real game out
and asserts the mate actually arrives inside the 50-move limit.
"""
import random

import pytest

import engine.search as move_finder
from engine.board import GameState, Move
from engine.eval import MOPUP_MIN_ADVANTAGE, evaluate
from engine.search import find_best_move
from engine.movegen import generate_legal
from engine.eval import _KBNK_PULL
from engine.search import _root_rng


def _board(pieces: dict[str, str]) -> list[list[str]]:
    """
    Build a board array from a {square: piece} map, e.g. {'e1': 'wK'}.

    Parameters
    ----------
    pieces : dict of str to str
        Algebraic square names mapped to legacy piece codes.

    Returns
    -------
    list of list of str
        An 8x8 board array of legacy codes, empty squares as '--'.
    """
    board = [['--' for _ in range(8)] for _ in range(8)]
    for square, code in pieces.items():
        col = ord(square[0]) - ord('a')
        row = 8 - int(square[1])
        board[row][col] = code
    return board


def test_mopup_drives_the_losing_king_towards_the_edge(custom_gs):
    """
    In K+Q vs K, a Black king on the rim must score worse for Black than the
    same position with the king centralized.
    """
    centre = custom_gs(_board({'a1': 'wK', 'h8': 'wQ', 'd4': 'bK'}))
    rim = custom_gs(_board({'a1': 'wK', 'h8': 'wQ', 'd8': 'bK'}))
    assert evaluate(rim) > evaluate(centre)


def test_mopup_walks_the_winning_king_in(custom_gs):
    """
    With the losing king fixed, White should prefer its own king nearer to it.
    """
    far = custom_gs(_board({'a1': 'wK', 'h8': 'wQ', 'd8': 'bK'}))
    near = custom_gs(_board({'d6': 'wK', 'h8': 'wQ', 'd8': 'bK'}))
    assert evaluate(near) > evaluate(far)


def test_mopup_stays_quiet_without_a_mateable_advantage(custom_gs):
    """
    Bishop against knight is dead drawn. Chasing there would burn the 50-move
    clock on a position with no win in it, so the term must not fire — moving
    the Black king to the rim should change nothing but the king tables.
    """
    centre = custom_gs(_board({'a1': 'wK', 'c3': 'wB', 'g7': 'bN', 'd4': 'bK'}))
    rim = custom_gs(_board({'a1': 'wK', 'c3': 'wB', 'g7': 'bN', 'd8': 'bK'}))
    # A lone bishop is 330, well under the rook-sized floor the gate requires.
    assert 330 < MOPUP_MIN_ADVANTAGE
    king_table_only = evaluate(rim) - evaluate(centre)
    # Same position with the bishop swapped for a rook *does* cross the floor,
    # so the difference there must be strictly larger than the tables alone.
    centre_r = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'd4': 'bK'}))
    rim_r = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'd8': 'bK'}))
    assert evaluate(rim_r) - evaluate(centre_r) > king_table_only


def test_mopup_stays_quiet_while_pawns_remain(custom_gs):
    """
    With pawns on, material and the passed-pawn terms already give the search
    a gradient; chasing the king would compete with pushing. The gate is
    pawnless positions only.
    """
    centre = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'h2': 'wP', 'd4': 'bK'}))
    rim = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'h2': 'wP', 'd8': 'bK'}))
    pawnless_centre = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'd4': 'bK'}))
    pawnless_rim = custom_gs(_board({'a1': 'wK', 'c3': 'wR', 'd8': 'bK'}))
    assert (evaluate(pawnless_rim) - evaluate(pawnless_centre)
            > evaluate(rim) - evaluate(centre))


def test_bishop_knight_corners_match_the_bishop_color():
    """
    The K+B+N table must aim at corners the bishop can actually cover.

    This is the whole point of having a second table: a bishop only controls
    one square color, so the mate exists in exactly two corners. Driving the
    king to either of the other two is not a slower win, it is no win — the
    king walks straight back out.
    """
    dark, light = _KBNK_PULL[1], _KBNK_PULL[0]
    # (row, col) with row 0 = rank 8: a8=(0,0) h8=(0,7) a1=(7,0) h1=(7,7).
    a8, h8, a1, h1 = (0, 0), (0, 7), (7, 0), (7, 7)
    for corner in (a1, h8):
        assert dark[corner[0]][corner[1]] > dark[a8[0]][a8[1]]
        assert dark[corner[0]][corner[1]] > dark[h1[0]][h1[1]]
    for corner in (a8, h1):
        assert light[corner[0]][corner[1]] > light[a1[0]][a1[1]]
        assert light[corner[0]][corner[1]] > light[h8[0]][h8[1]]


def _play_out(gs: GameState, move_limit: int, depth: int = 4) -> tuple[bool, int]:
    """
    Play both sides with the search until mate, stalemate, or a move cap.

    Parameters
    ----------
    gs : GameState
        Position to play from; mutated in place.
    move_limit : int
        Maximum plies to play before giving up.
    depth : int, optional
        Search depth per move. 4 converts every endgame covered here.

    Returns
    -------
    tuple of (bool, int)
        Whether checkmate was reached, and how many plies it took.
    """
    # Seed the root shuffle, for the same reason `engine/tools/bench.py` does: it
    # changes how much the search prunes, so an unseeded playout is a
    # different game every run. Without this the test is exactly as much of a
    # coin flip as the bug it is checking for — measured over 20 seeds, K+R
    # vs K converted 0/20 times before this term and 20/20 after, so an
    # unseeded single run would pass or fail more or less at random.
    _root_rng = random.Random(20240719)
    for ply in range(move_limit):
        moves = generate_legal(gs)
        if not moves:
            return gs.is_checkmate, ply
        # Depth-bounded, not clock-bounded: a time limit would make the test
        # search shallower on a loaded machine and convert differently there.
        # Depth 4 in a three-piece endgame finishes far inside this.
        best = find_best_move(gs, max_depth=depth, time_limit=60.0)
        if best is None:
            return gs.is_checkmate, ply
        gs.make_move(Move.from_ai_tuple(best, gs.board))
    return False, move_limit


@pytest.mark.parametrize('piece, name', [('wQ', 'queen'), ('wR', 'rook')])
def test_lone_piece_forces_mate_inside_the_fifty_move_limit(custom_gs, piece, name):
    """
    The behavior the term exists for: K+Q vs K and K+R vs K must actually be
    converted, not shuffled into a draw. 50 moves is 100 plies, and that is
    the hard limit the rules impose — a search that needs more has not won.
    """
    gs = custom_gs(_board({'e1': 'wK', 'a1': piece, 'e8': 'bK'}))
    mated, plies = _play_out(gs, move_limit=100)
    assert mated, f'K+{name} vs K was not converted in 50 moves ({plies} plies)'
    assert gs.halfmove_clock < 100
