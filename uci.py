"""
UCI (Universal Chess Interface) adapter for the PyCheckmate engine.

This module lets the engine talk to any UCI-compatible host: chess GUIs
(Cute Chess, Arena, BanksiaGUI) and, most importantly, the `lichess-bot`
bridge that connects UCI engines to the Lichess Bot API.

Run it directly and type commands, or point a GUI/bridge at it:

    python uci.py

Supported commands: `uci`, `isready`, `ucinewgame`,
`position startpos|fen <fen> [moves <uci>...]`, `go [depth N] [movetime MS]`,
`quit`.

AI_PLANNING: This file is step 2 of the Lichess roadmap (see
LICHESS_BOT_PLAN.md). The minimal command set below is everything
lichess-bot strictly needs; the marked extension points cover clock-aware
time management and pondering for stronger online play.
"""
import sys

import chess_engine
import move_finder

ENGINE_NAME = 'PyCheckmate'
ENGINE_AUTHOR = 'Lucas Pham'

# Search defaults when the host gives no explicit limits
DEFAULT_DEPTH = 5
DEFAULT_MOVETIME = 5.0  # seconds


def apply_uci_move(gs: chess_engine.GameState, uci_str: str) -> bool:
    """
    Apply a move given in UCI coordinate notation to the game state.

    Parameters
    ----------
    gs : chess_engine.GameState
        The game state to mutate.
    uci_str : str
        The move in UCI notation (e.g., 'e2e4', 'e7e8q').

    Returns
    -------
    bool
        True if the move matched a legal move and was applied, else False.
    """
    for move in gs.get_valid_moves():
        if move.get_uci_notation() == uci_str:
            gs.make_move(move, annotate=False)
            return True
    return False


def build_position(tokens: list[str]) -> chess_engine.GameState:
    """
    Build a GameState from the arguments of a UCI `position` command.

    Parameters
    ----------
    tokens : list of str
        The command arguments after 'position', e.g.
        ['startpos', 'moves', 'e2e4', 'e7e5'] or
        ['fen', <6 FEN fields...>, 'moves', ...].

    Returns
    -------
    chess_engine.GameState
        The reconstructed game state after all listed moves.

    Raises
    ------
    ValueError
        If the position description or one of the moves is invalid.
    """
    if not tokens:
        raise ValueError('position: missing arguments')

    if tokens[0] == 'startpos':
        gs = chess_engine.GameState()
        move_tokens = tokens[2:] if len(tokens) > 1 and tokens[1] == 'moves' else []
    elif tokens[0] == 'fen':
        # FEN is 6 space-separated fields; 'moves' may follow
        if 'moves' in tokens:
            moves_at = tokens.index('moves')
            fen = ' '.join(tokens[1:moves_at])
            move_tokens = tokens[moves_at + 1:]
        else:
            fen = ' '.join(tokens[1:])
            move_tokens = []
        gs = chess_engine.GameState.from_fen(fen)
    else:
        raise ValueError(f'position: unknown mode {tokens[0]!r}')

    for uci_move in move_tokens:
        if not apply_uci_move(gs, uci_move):
            raise ValueError(f'position: illegal move {uci_move!r}')
    return gs


def handle_go(gs: chess_engine.GameState, tokens: list[str]) -> str:
    """
    Execute a UCI `go` command and return the chosen move in UCI notation.

    Parameters
    ----------
    gs : chess_engine.GameState
        The game state to search from.
    tokens : list of str
        The command arguments after 'go' (e.g., ['depth', '4'] or
        ['movetime', '3000']).

    Returns
    -------
    str
        The best move in UCI notation, or '0000' when no legal move exists.
    """
    depth = DEFAULT_DEPTH
    movetime = DEFAULT_MOVETIME

    it = iter(range(len(tokens)))
    for i in it:
        if tokens[i] == 'depth' and i + 1 < len(tokens):
            depth = int(tokens[i + 1])
        elif tokens[i] == 'movetime' and i + 1 < len(tokens):
            movetime = int(tokens[i + 1]) / 1000.0
        # AI_PLANNING: parse 'wtime'/'btime'/'winc'/'binc' here and derive a
        # per-move budget (e.g., remaining_time / 30 + increment). Lichess
        # sends these on every 'go', so real clock handling lives here.

    best = move_finder.find_best_move(gs, max_depth=depth, time_limit=movetime)
    if best is None:
        return '0000'  # UCI null move: no legal moves available

    return chess_engine.Move.from_ai_tuple(best, gs.board).get_uci_notation()


def main() -> None:
    """
    Run the UCI read-eval-print loop over stdin/stdout until `quit`.

    Returns
    -------
    None
    """
    gs = chess_engine.GameState()

    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        command, args = parts[0], parts[1:]

        if command == 'uci':
            print(f'id name {ENGINE_NAME}')
            print(f'id author {ENGINE_AUTHOR}')
            # AI_PLANNING: declare configurable options here with 'option name
            # ... type ...' lines (hash size, skill level) once supported
            print('uciok')

        elif command == 'isready':
            print('readyok')

        elif command == 'ucinewgame':
            gs = chess_engine.GameState()

        elif command == 'position':
            try:
                gs = build_position(args)
            except ValueError as exc:
                print(f'info string {exc}')

        elif command == 'go':
            best_uci = handle_go(gs, args)
            print(f'bestmove {best_uci}')

        elif command == 'quit':
            break

        # AI_PLANNING: implement 'stop' (abort an async search and answer with
        # the best move so far) and 'ponderhit' once the search runs on a
        # background thread inside this adapter

        sys.stdout.flush()


if __name__ == '__main__':
    main()
