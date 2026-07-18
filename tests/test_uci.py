"""
Test suite for the UCI adapter's search-limit handling (uci.py).

Lichess sends clock fields (`wtime`/`btime`/
`winc`/`binc`) on every `go`, and the adapter must turn them into a sensible
per-move time budget instead of falling back to fixed defaults. These tests
exercise the pure budgeting/parsing functions directly — no search needed —
plus one end-to-end `handle_go` call per interesting path.
"""
from engine import uci
from engine.chess_engine import GameState, Move


# --- clock_move_budget: remaining / moves-left + 0.8*increment -------------

def test_increment_adds_to_the_budget():
    """The increment is time that comes back after every move, so most of it
    can be spent on top of the clock's share rather than hoarded."""
    without = uci.clock_move_budget(600_000, 0)
    with_inc = uci.clock_move_budget(600_000, 5_000)
    assert with_inc == without + 5.0 * uci.INCREMENT_WEIGHT


def test_budget_divides_clock_by_the_moves_still_expected():
    """A 3-minute clock at game start spreads over the expected game length,
    minus the overhead and reserve that the search never gets to use."""
    usable = 180.0 - uci.MOVE_OVERHEAD - uci.CLOCK_RESERVE
    assert uci.clock_move_budget(180_000, 0) == usable / uci.EXPECTED_GAME_MOVES


def test_budget_grows_as_the_game_goes_on():
    """The point of the moves-to-go estimate: with the same clock, a move
    deeper into the game gets a bigger share, because fewer moves remain to
    spread it over. The old constant divisor did the opposite — it shrank
    every move and left a third of the clock unspent at the end."""
    early = uci.clock_move_budget(180_000, 0, moves_played=5)
    late = uci.clock_move_budget(180_000, 0, moves_played=30)
    assert late > early


def test_budget_hoards_when_the_clock_runs_low():
    """Below LOW_CLOCK_SECONDS survival outranks thinking, so the same
    position budgets a smaller *fraction* of what is left."""
    low = uci.LOW_CLOCK_SECONDS - 1.0
    fraction_low = uci.clock_move_budget(int(low * 1000), 0, moves_played=40) / low
    fraction_normal = uci.clock_move_budget(120_000, 0, moves_played=40) / 120.0
    assert fraction_low < fraction_normal


def test_budget_clamps_low_when_flagging():
    """With 1 second left the floor keeps the engine from thinking for 33ms
    (too little to finish even depth 1 reliably) but far below the clock."""
    assert uci.clock_move_budget(1_000, 0) == uci.MIN_MOVE_TIME


def test_budget_clamps_high_on_long_clocks():
    """A classical clock must not tempt the engine into 60-second thinks."""
    assert uci.clock_move_budget(3_600_000 * 3, 0) == uci.MAX_MOVE_TIME


# --- parse_go_limits: which numbers win ------------------------------------

def test_explicit_depth_and_movetime_win():
    """`go depth 3 movetime 2000` must be honored exactly as before — an
    explicit budget is a promise, so the hard limit equals it (no panic
    extension)."""
    assert uci.parse_go_limits(['depth', '3', 'movetime', '2000'], True) == (3, 2.0, 2.0)


def test_no_arguments_fall_back_to_defaults():
    """A bare `go` falls back to the fixed defaults."""
    assert uci.parse_go_limits([], True) == (
        uci.DEFAULT_DEPTH, uci.DEFAULT_MOVETIME, uci.DEFAULT_MOVETIME)


def test_clock_fields_drive_budget_for_white():
    """With only clock fields, White's clock sets the budget and the depth
    cap is lifted so the timer — not the depth — ends the search. The hard
    limit funds the panic extension: PANIC_HARD_FACTOR times the budget,
    but never more than 1/PANIC_CLOCK_DIVISOR of the remaining clock."""
    tokens = ['wtime', '180000', 'btime', '5000', 'winc', '0', 'binc', '0']
    depth, movetime, hard = uci.parse_go_limits(tokens, True)
    expected = uci.clock_move_budget(180_000, 0)
    assert depth == uci.CLOCK_MAX_DEPTH
    assert movetime == expected
    assert hard == min(uci.PANIC_HARD_FACTOR * expected,
                       180.0 / uci.PANIC_CLOCK_DIVISOR)


def test_clock_fields_use_black_clock_when_black_moves():
    """The same tokens must budget from btime/binc for Black: 1s left with
    no increment hits the MIN_MOVE_TIME floor, not White's 6 seconds — and
    the panic ceiling shrinks with the clock (1/PANIC_CLOCK_DIVISOR of the
    1s left) but never below the budget itself."""
    tokens = ['wtime', '180000', 'btime', '1000', 'winc', '0', 'binc', '0']
    depth, movetime, hard = uci.parse_go_limits(tokens, False)
    assert depth == uci.CLOCK_MAX_DEPTH
    assert movetime == uci.MIN_MOVE_TIME
    assert hard == max(uci.MIN_MOVE_TIME, 1.0 / uci.PANIC_CLOCK_DIVISOR)


def test_movetime_beats_clock_fields():
    """An explicit movetime overrides any clock arithmetic, including the
    panic ceiling (hard == movetime: no extension on promised budgets)."""
    tokens = ['movetime', '1500', 'wtime', '600000', 'winc', '5000']
    assert uci.parse_go_limits(tokens, True) == (uci.DEFAULT_DEPTH, 1.5, 1.5)


def test_explicit_depth_kept_alongside_clock():
    """`go depth 2 wtime ...` searches to depth 2 within the clock budget
    (some GUIs combine both; the smaller constraint should win naturally).
    The clock path still funds a panic ceiling, well under the 180s/8 clock
    cap."""
    tokens = ['depth', '2', 'wtime', '180000', 'btime', '180000']
    expected = uci.clock_move_budget(180_000, 0)
    assert uci.parse_go_limits(tokens, True) == (
        2, expected, uci.PANIC_HARD_FACTOR * expected)


# --- handle_go end to end --------------------------------------------------

def test_handle_go_with_clock_returns_legal_move():
    """A Lichess-style `go` with a nearly empty clock must still answer
    quickly with a legal move (the MIN_MOVE_TIME floor in action)."""
    gs = GameState()
    tokens = ['wtime', '1000', 'btime', '1000', 'winc', '0', 'binc', '0']
    best = uci.handle_go(gs, tokens)

    legal = {
        Move.from_ai_tuple(move, gs.board).get_uci_notation()
        for move in gs.get_valid_moves(for_ai=True)
    }
    assert best in legal


def test_handle_go_reports_null_move_when_mated():
    """After Fool's Mate White has no legal moves, so `go` answers 0000."""
    gs = GameState.from_fen('rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3')
    assert uci.handle_go(gs, ['depth', '1']) == '0000'
