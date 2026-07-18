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

    Raises
    ------
    EngineClientError
        If the process cannot be spawned or fails the UCI handshake.
    """

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self._lock = threading.Lock()
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=PROJECT_ROOT,
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

    def close(self) -> None:
        """Shut the engine down, escalating politely: quit -> kill."""
        if self._proc.poll() is None:
            try:
                self._send('quit')
                self._proc.wait(timeout=RESPONSE_GRACE)
            except (EngineClientError, subprocess.TimeoutExpired):
                self._proc.kill()
