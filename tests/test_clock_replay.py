"""
Tests for the offline clock replay.

The tool exists to answer one question — does raising `MIN_MOVES_TO_GO` move
thinking time into the endgame — so the tests pin that answer's *direction* and
the physical constraint that makes the simulation meaningful.
"""
from engine import uci
from engine.tools.clock_replay import PHASES, parse_time_control, replay_game


def _record(our_moves: int, time_control: str = '300+0') -> dict:
    """
    Build a synthetic game record with a given number of our moves.

    Parameters
    ----------
    our_moves : int
        How many moves we played.
    time_control : str, optional
        PGN time control string.

    Returns
    -------
    dict
        A record shaped like one line of `game_analysis.jsonl`.
    """
    moves = []
    for i in range(our_moves):
        moves.append({'ply': 2 * i, 'move_number': i + 1, 'ours': True,
                      'seconds': 1.0})
        moves.append({'ply': 2 * i + 1, 'move_number': i + 1, 'ours': False,
                      'seconds': 1.0})
    return {'time_control': time_control, 'moves': moves}


def _endgame_share(record: dict, setting: int) -> float:
    """
    Fraction of the simulated budget that lands in the last phase bucket.

    Parameters
    ----------
    record : dict
        A game record.
    setting : int
        The `MIN_MOVES_TO_GO` value to simulate.

    Returns
    -------
    float
        Endgame share of the total allocated time.
    """
    budgets = replay_game(record, setting)
    total = sum(b for _n, b in budgets)
    late = sum(b for n, b in budgets if n >= PHASES[-1][0])
    return late / total


def test_a_higher_floor_moves_time_into_the_endgame():
    """
    The claim the constant was raised to make, and the one the first version of
    this tool got backwards.

    Reconstructing the clock from what games *actually* spent breaks the
    feedback loop the change relies on — a bigger divisor is a thinner slice
    now, but leaves a larger clock alive later. Measured against a fixed
    historical trajectory, every setting sees the same remaining clock, so a
    bigger divisor can only look worse at every phase, by construction. The
    simulation has to evolve the clock from the budgets being tested.
    """
    record = _record(our_moves=80)
    shares = [_endgame_share(record, setting) for setting in (18, 28, 40)]
    assert shares[0] < shares[1] < shares[2], shares


def test_the_simulated_clock_is_never_overspent():
    """
    A budget that outruns the clock would invent time and make every share
    meaningless. Spending must fit inside the starting clock plus increments.
    """
    for tc in ('300+0', '600+2', '60+0'):
        initial, increment = parse_time_control(tc)
        record = _record(our_moves=120, time_control=tc)
        budgets = replay_game(record, uci.MIN_MOVES_TO_GO)
        spent = sum(b for _n, b in budgets)
        assert spent <= initial + increment * len(budgets) + 1e-6, tc


def test_a_game_with_no_usable_clock_is_skipped():
    """
    Correspondence games record `-` as their time control. Returning an empty
    list keeps them out of the averages instead of dividing by a zero clock.
    """
    assert parse_time_control('-') == (0.0, 0.0)
    assert replay_game(_record(our_moves=10, time_control='-'), 28) == []
