"""
Shared configuration and fixtures for pytest.
Pytest automatically discovers this file; no need to import these fixtures manually.
"""
import pytest
from engine.chess_engine import CODE_TO_INT, GameState


@pytest.fixture
def gs():
    """
    Provide a fresh GameState instance with the standard initial board setup.
    """
    return GameState()


@pytest.fixture
def custom_gs():
    """
    Provide a factory function to set up a GameState with a custom board array.
    Automatically recalculates the piece tracking sets, King locations,
    Zobrist key, and draw-rule tracking logs.

    Usage in tests:
        gs = custom_gs(empty_board_array, white_turn=True)
    """

    def _setup(board_array: list[list], white_turn: bool = True) -> GameState:
        game_state = GameState()
        # Tests hand-build boards with the legacy 'wP'/'--' codes for
        # readability; convert them to the engine's integer encoding here, the
        # single choke point every hand-built fixture already flows through.
        game_state.board = [
            [CODE_TO_INT[cell] if isinstance(cell, str) else cell for cell in row]
            for row in board_array
        ]
        game_state.white_to_move = white_turn
        game_state.halfmove_clock = 0

        # Rebuild every derived cache (piece sets, king locations, zobrist,
        # repetition logs) from the raw board in one place
        game_state.refresh_derived_state()

        return game_state

    return _setup


@pytest.fixture
def empty_kings_gs(custom_gs):
    """
    Provide a GameState with an empty board containing only two Kings.
    Useful for quickly placing pieces for isolated edge case testing.
    """
    empty_board = [['--' for _ in range(8)] for _ in range(8)]
    empty_board[7][4] = 'wK'
    empty_board[0][4] = 'bK'
    return custom_gs(empty_board)
