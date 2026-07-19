"""
Client side of the UCI protocol: run the engine as a separate process.

The GUI must stay on CPython (pygame has no PyPy wheels), but the engine
itself is pure stdlib, so it can run inside a PyPy interpreter where the
JIT makes the search roughly 2x faster (and more at longer time controls).
This module spawns `engine.uci` under the best interpreter it can find and
talks to it over the same UCI text protocol that Lichess bridges use —
so the GUI exercises exactly the code path the bot will.

The subprocess is kept alive between moves on purpose: PyPy's JIT compiles
the hot search loops during the first searches, and later moves in the
game get the fully warmed-up speed.
"""
import shutil
import subprocess
import threading
from pathlib import Path

# The engine runs as `python -m engine.uci`, which needs the project root
# (this package's parent) as working directory; resolve it absolutely so
# the subprocess works no matter what the GUI's working directory is
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UCI_MODULE_ARGS = ['-m', 'engine.uci']

# Grace period added on top of the search movetime before we declare the
# engine unresponsive (protocol handshakes use it directly)
RESPONSE_GRACE = 5.0

# A fixed-depth analysis has no movetime to derive a deadline from, so the
# lock wait gets its own generous bound — a deep Stockfish search on a
# complex position can legitimately take a while.
ANALYSIS_TIMEOUT = 120.0


class EngineClientError(Exception):
    """Raised when the engine process dies or breaks protocol."""


def resolve_engine_command() -> list[str] | None:
    """
    Find the best interpreter to host the UCI engine subprocess.

    Preference order: a PyPy on PATH, then a uv-managed PyPy (uv downloads
    PyPy to its own directory, so it is not on PATH). Returns None when no
    PyPy exists — callers should then search in-process instead, which
    avoids paying subprocess overhead for the same CPython speed.

    Returns
    -------
    list[str] | None
        Command vector to spawn the engine, or None if only CPython exists.
    """
    pypy = shutil.which('pypy3') or shutil.which('pypy')
    if pypy:
        return [pypy, *UCI_MODULE_ARGS]

    if shutil.which('uv'):
        try:
            found = subprocess.run(
                ['uv', 'python', 'find', 'pypy'],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        path = found.stdout.strip()
        if found.returncode == 0 and path:
            return [path, *UCI_MODULE_ARGS]

    return None


class UciEngineClient:
    """
    A persistent UCI engine subprocess with a blocking search interface.

    All protocol I/O happens under one lock, so a search request issued
    while a stale search is still finishing simply waits its turn — the
    pipe never interleaves two conversations.

    Parameters
    ----------
    command : list[str]
        Command vector to spawn the engine (interpreter + module args).
    cwd : str, optional
        Working directory for the subprocess. Defaults to this repo's root;
        pointing it at another checkout runs *that* checkout's engine — which
        is how `engine.abtest` pits two versions against each other.

    Raises
    ------
    EngineClientError
        If the process cannot be spawned or fails the UCI handshake.
    """

    def __init__(self, command: list[str], cwd: str | None = None) -> None:
        self.command = command
        self._lock = threading.Lock()
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd or PROJECT_ROOT,
                text=True,
                bufsize=1,  # Line-buffered: each protocol line flushes immediately
            )
        except OSError as exc:
            raise EngineClientError(f'could not spawn engine: {exc}') from exc

        # Standard UCI handshake: identify, then wait until fully ready
        with self._lock:
            self._send('uci')
            self._read_until('uciok')
            self._send('isready')
            self._read_until('readyok')

    def _send(self, line: str) -> None:
        """Write one protocol line to the engine (caller holds the lock)."""
        if self._proc.poll() is not None or self._proc.stdin is None:
            raise EngineClientError('engine process is not running')
        try:
            self._proc.stdin.write(line + '\n')
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise EngineClientError(f'engine pipe closed: {exc}') from exc

    def _read_until(self, prefix: str) -> str:
        """Read protocol lines until one starts with `prefix` and return it."""
        if self._proc.stdout is None:
            raise EngineClientError('engine has no stdout pipe')
        while True:
            line = self._proc.stdout.readline()
            if line == '':  # EOF: the engine process died mid-conversation
                raise EngineClientError('engine process closed its output')
            line = line.strip()
            if line.startswith(prefix):
                return line

    def new_game(self) -> None:
        """Tell the engine a fresh game starts (clears its search state)."""
        with self._lock:
            self._send('ucinewgame')
            self._send('isready')
            self._read_until('readyok')

    def search_from_moves(
        self, uci_moves: list[str], depth: int, movetime: float
    ) -> str:
        """
        Search the position reached from the start position via `uci_moves`.

        Sending the whole move list (rather than a bare FEN) lets the engine
        replay the game and keep its repetition history accurate.

        Parameters
        ----------
        uci_moves : list[str]
            Every move played so far, in UCI coordinate notation.
        depth : int
            Maximum search depth in plies.
        movetime : float
            Soft time limit for the search, in seconds.

        Returns
        -------
        str
            The engine's best move in UCI notation (e.g. 'e2e4', 'a7a8q').

        Raises
        ------
        EngineClientError
            If the engine is busy past its deadline, dies, or answers
            with no playable move.
        """
        # A previous (invalidated) search may still hold the lock; it can
        # run for at most `movetime`, so waiting a bounded amount is safe.
        # Failing to get the lock means the engine is wedged -> caller
        # falls back to the in-process search.
        if not self._lock.acquire(timeout=movetime + RESPONSE_GRACE):
            raise EngineClientError('engine is busy past its deadline')
        try:
            position = 'position startpos'
            if uci_moves:
                position += ' moves ' + ' '.join(uci_moves)
            self._send(position)
            self._send(f'go depth {depth} movetime {int(movetime * 1000)}')
            reply = self._read_until('bestmove')
        finally:
            self._lock.release()

        parts = reply.split()
        if len(parts) < 2 or parts[1] in ('(none)', '0000'):
            raise EngineClientError(f'engine returned no move: {reply!r}')
        return parts[1]

    def analyse(self, uci_moves: list[str], depth: int) -> tuple[str, int, bool]:
        """
        Search a position and return the *score* as well as the best move.

        `search_from_moves` throws away the `info` lines and keeps only
        `bestmove`, which is all a player needs. An analyst needs the
        evaluation, so this keeps the last score reported before the engine
        committed to its move. Written for driving Stockfish as a reference
        engine (`engine.sf_review`), but it is plain UCI and works against
        our own engine too.

        Parameters
        ----------
        uci_moves : list[str]
            Every move from the start position to the one being analysed.
        depth : int
            Fixed search depth. Depth rather than time keeps the numbers
            reproducible across runs and machines.

        Returns
        -------
        tuple of (str, int, bool)
            `(best_move, score, is_mate)`. `score` is centipawns from the
            **side to move's** perspective, or, when `is_mate` is True, the
            number of moves to mate (signed the same way). Callers must
            convert to a common perspective themselves.

        Raises
        ------
        EngineClientError
            If the engine dies or reports no score at all.
        """
        if not self._lock.acquire(timeout=ANALYSIS_TIMEOUT):
            raise EngineClientError('engine is busy past its deadline')
        try:
            position = 'position startpos'
            if uci_moves:
                position += ' moves ' + ' '.join(uci_moves)
            self._send(position)
            self._send(f'go depth {depth}')

            # Keep the most recent score seen; the last one before `bestmove`
            # is the deepest completed iteration's verdict.
            score: int | None = None
            is_mate = False
            if self._proc.stdout is None:
                raise EngineClientError('engine has no stdout pipe')
            while True:
                line = self._proc.stdout.readline()
                if line == '':
                    raise EngineClientError('engine process closed its output')
                line = line.strip()
                if line.startswith('bestmove'):
                    break
                if ' score ' in line:
                    parts = line.split()
                    kind = parts[parts.index('score') + 1]
                    value = int(parts[parts.index('score') + 2])
                    score, is_mate = value, kind == 'mate'
        finally:
            self._lock.release()

        if score is None:
            raise EngineClientError(f'engine reported no score: {line!r}')
        parts = line.split()
        best = parts[1] if len(parts) > 1 else '0000'
        return best, score, is_mate

    def set_option(self, name: str, value: str | int) -> None:
        """
        Set a UCI option (e.g. Stockfish's ``Threads`` or ``Hash``).

        Parameters
        ----------
        name : str
            Option name, exactly as the engine spells it.
        value : str or int
            Value to set.
        """
        with self._lock:
            self._send(f'setoption name {name} value {value}')
            self._send('isready')
            self._read_until('readyok')

    def close(self) -> None:
        """Shut the engine down, escalating politely: quit -> kill."""
        if self._proc.poll() is None:
            try:
                self._send('quit')
                self._proc.wait(timeout=RESPONSE_GRACE)
            except (EngineClientError, subprocess.TimeoutExpired):
                self._proc.kill()
