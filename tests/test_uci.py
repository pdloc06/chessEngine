"""
Test suite for the UCI adapter's search-limit handling (uci.py).

Step 5 of LICHESS_BOT_PLAN.md: Lichess sends clock fields (`wtime`/`btime`/
`winc`/`binc`) on every `go`, and the adapter must turn them into a sensible
per-move time budget instead of falling back to fixed defaults. These tests
exercise the pure budgeting/parsing functions directly — no search needed —
plus one end-to-end `handle_go` call per interesting path.
"""
from engine import uci
from engine.chess_engine import GameState, Move


# --- clock_move_budget: the remaining/30 + 0.8*increment heuristic ---------

def test_budget_formula_midgame_clock():
    """10 minutes + 5s increment: 600/30 + 5*0.8 = 24s, capped at 20s."""
    assert uci.clock_move_budget(600_000, 5_000) == uci.MAX_MOVE_TIME


def test_budget_formula_no_increment():
    """A 3-minute clock with no increment budgets 180/30 = 6 seconds."""
    assert uci.clock_move_budget(180_000, 0) == 6.0


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
    """A bare `go` keeps the pre-step-5 behavior."""
    assert uci.parse_go_limits([], True) == (
        uci.DEFAULT_DEPTH, uci.DEFAULT_MOVETIME, uci.DEFAULT_MOVETIME)


def test_clock_fields_drive_budget_for_white():
    """With only clock fields, White's clock sets the budget and the depth
    cap is lifted so the timer — not the depth — ends the search. The hard
    limit funds the panic extension: PANIC_HARD_FACTOR times the budget,
    but never more than 1/PANIC_CLOCK_DIVISOR of the remaining clock."""
    tokens = ['wtime', '180000', 'btime', '5000', 'winc', '0', 'binc', '0']
    depth, movetime, hard = uci.parse_go_limits(tokens, True)
    assert depth == uci.CLOCK_MAX_DEPTH
    assert movetime == 6.0
    assert hard == min(uci.PANIC_HARD_FACTOR * 6.0, 180.0 / uci.PANIC_CLOCK_DIVISOR)


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
    The clock path still funds a panic ceiling: 2.5 x 6s, well under the
    180s/8 clock cap."""
    tokens = ['depth', '2', 'wtime', '180000', 'btime', '180000']
    assert uci.parse_go_limits(tokens, True) == (2, 6.0, uci.PANIC_HARD_FACTOR * 6.0)


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
