"""
AI engine implementation utilizing Minmax with Alpha-Beta pruning.
Evaluates positions iteratively and leverages lightweight move tuples.
"""
import random

# Example static evaluation dictionary (Modify with your own piece values)
PIECE_VALUES = {'K': 0, 'Q': 900, 'R': 500, 'B': 300, 'N': 300, 'P': 100}


def find_best_move(gs, valid_moves: list[tuple], depth: int = 3) -> tuple:
    """
    Entry point for the AI. Evaluates available moves and returns the optimal one.

    Parameters
    ----------
    gs : GameState
        The current game state object.
    valid_moves : list of tuple
        Pre-calculated list of valid AI move tuples.
    depth : int
        The search depth for the Minimax algorithm.

    Returns
    -------
    tuple
        The best evaluated move tuple.
    """
    global next_move
    next_move = None
    random.shuffle(valid_moves)  # Shuffle to add variety on equal evaluations

    # Starting parameters for alpha-beta bounds
    alpha = -float('inf')
    beta = float('inf')

    # 1 for White (maximizing), -1 for Black (minimizing)
    turn_multiplier = 1 if gs.white_to_move else -1

    _find_move_minimax(gs, valid_moves, depth, alpha, beta, turn_multiplier)

    return next_move


def _find_move_minimax(
    gs,
    valid_moves: list[tuple],
    depth: int,
    alpha: float,
    beta: float,
    turn_multiplier: int
) -> float:
    """
    Recursive core of the Minimax algorithm with Alpha-Beta pruning.

    Parameters
    ----------
    gs : GameState
        The current game state object being evaluated.
    valid_moves : list of tuple
        Pre-calculated list of valid lightweight AI move tuples at the current node.
    depth : int
        The remaining search depth for the Minimax algorithm.
    alpha : float
        The best score the maximizing player can guarantee.
    beta : float
        The best score the minimizing player can guarantee.
    turn_multiplier : int
        Multiplier to adjust scores based on the current player (1 for White, -1 for Black).

    Returns
    -------
    float
        The evaluated maximum or minimum score for the current branch.
    """
    global next_move

    if depth == 0:
        return turn_multiplier * score_board(gs)

    max_score = -float('inf')

    for move in valid_moves:
        # 1. Execute lightweight move and capture state package
        undo_package = gs.make_ai_move(move)

        # 2. Retrieve responses (Must ensure get_valid_moves supports for_ai=True)
        next_moves = gs.get_valid_moves(for_ai=True)

        # 3. Recursive evaluation
        score = -_find_move_minimax(gs, next_moves, depth - 1, -beta, -alpha, -turn_multiplier)

        # 4. Reverse the state exactly using the tracked package
        gs.unmake_ai_move(move, undo_package)

        # 5. Alpha-Beta Pruning logic
        if score > max_score:
            max_score = score
            if depth == 3:  # Assuming top-level depth matching
                next_move = move

        if max_score > alpha:
            alpha = max_score

        if alpha >= beta:
            break  # Prune the remaining branches

    return max_score


def score_board(gs) -> float:
    """
    Static evaluation function analyzing material advantage.
    Positive score favors White, negative favors Black.

    Returns
    -------
    float
        The calculated numerical evaluation of the board.
    """
    if gs.is_checkmate:
        return -20000 if gs.white_to_move else 20000
    elif gs.is_stalemate:
        return 0

    score = 0
    for row in gs.board:
        for square in row:
            if square[0] == 'w':
                score += PIECE_VALUES.get(square[1], 0)
            elif square[0] == 'b':
                score -= PIECE_VALUES.get(square[1], 0)
    return score