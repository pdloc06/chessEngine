"""
Test suite for the UCI engine subprocess client (uci_client.py).

The client is spawned with the current CPython interpreter, so these tests
run everywhere; when PyPy is installed the GUI uses the exact same protocol
path, just with a different interpreter hosting uci.py.
"""
import sys

import pytest

from engine import uci_client
from engine.chess_engine import GameState, Move


@pytest.fixture(scope='module')
def client():
    """Provide one shared engine subprocess for the whole module (spawning
    an interpreter per test would dominate the suite's runtime)."""
    engine = uci_client.UciEngineClient([sys.executable, *uci_client.UCI_MODULE_ARGS])
    yield engine
    engine.close()


def test_search_from_startpos_returns_legal_move(client):
    """Verify the engine answers with a move that is legal in the position."""
    best = client.search_from_moves([], depth=2, movetime=3.0)

    gs = GameState()
    legal = {
        Move.from_ai_tuple(move, gs.board).get_uci_notation()
        for move in gs.get_valid_moves(for_ai=True)
    }
    assert best in legal


def test_search_replays_move_list(client):
    """Verify the engine finds the mate after replaying a game's moves.

    After 1. f3 e5 2. g4 the only sane answer is Qh4#, so the reply also
    proves the 'position startpos moves ...' replay reached the right
    position (any desync would make d8h4 illegal or unattractive).
    """
    best = client.search_from_moves(['f2f3', 'e7e5', 'g2g4'], depth=2, movetime=3.0)
    assert best == 'd8h4'


def test_new_game_resets_cleanly(client):
    """Verify ucinewgame keeps the protocol conversational."""
    client.new_game()
    best = client.search_from_moves(['e2e4'], depth=1, movetime=3.0)
    assert len(best) in (4, 5)  # e7e5-style, or a7a8q-style with promotion


def test_dead_engine_raises():
    """Verify a closed engine surfaces EngineClientError, not a hang."""
    engine = uci_client.UciEngineClient([sys.executable, *uci_client.UCI_MODULE_ARGS])
    engine.close()
    with pytest.raises(uci_client.EngineClientError):
        engine.search_from_moves([], depth=1, movetime=1.0)


def test_resolve_engine_command_shape():
    """Verify auto-detection returns a spawnable command vector or None."""
    command = uci_client.resolve_engine_command()
    assert command is None or (
        isinstance(command, list)
        and command[1:] == uci_client.UCI_MODULE_ARGS
    )
