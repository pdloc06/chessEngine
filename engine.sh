#!/bin/sh
# Template launcher for lichess-bot (step 4 of LICHESS_BOT_PLAN.md).
#
# lichess-bot expects its engine to be a plain executable, not a Python
# module, so this shim cd's into the repo and execs the UCI adapter there.
# To use it: copy this file into lichess-bot's engines/ directory, replace
# the path below with the absolute path to this repo, chmod +x it, and set
# engine.name: "engine.sh" in lichess-bot's config.yml.
#
# PyPy roughly doubles search speed (see LICHESS_BOT_PLAN.md step 6); if it
# is not installed, `uv python install pypy3.11` fetches it, or drop the
# `-p pypy3.11` to fall back to CPython.
cd /path/to/PyCheckmate && exec uv run --no-project -p pypy3.11 python -m engine.uci
