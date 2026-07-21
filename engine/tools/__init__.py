"""
Measurement and operations tooling: everything that *studies* the engine
rather than being part of it.

The split is not cosmetic. `engine/` proper is pure stdlib so that it runs
under PyPy, where the JIT roughly doubles search speed, and so that the UCI
subprocess can be hosted there. These modules are under no such constraint —
they shell out to `stockfish` and `fastchess`, read game records, and are only
ever run by hand or by the `bot` script.

The dependency runs one way: tools import from the engine, the engine never
imports from tools.

Modules
-------
bench     : node-count determinism check and interpreter speed comparison
calibrate : absolute strength against UCI_Elo-limited Stockfish
sprt      : fastchess wrapper for sequential probability ratio tests
selfplay  : engine-vs-engine games
sf_review : Stockfish grading of a directory of games
sf_watch  : background watcher that grades each game as the bot finishes it
"""
