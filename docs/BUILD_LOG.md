# PyCheckmate — Build Log

A running record of how this engine was built: what was added, what broke, what
was measured, and what got thrown away. Kept incrementally, not reconstructed at
the end.

**Status legend:** ✅ shipped · ❌ measured and reverted · ⏳ in progress

---

## Project Overview

**What it is.** A chess engine written from scratch in Python, with three faces:
a Pygame desktop game, a chess.com-style post-game review screen, and a UCI
adapter that runs the same engine as a bot on Lichess.

**Stack.**

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.14 | Learning project — clarity over raw speed |
| Engine core | **Pure stdlib, zero dependencies** | Lets the engine run under PyPy |
| Runtime accel | PyPy 3.11 subprocess | JIT gives a large speedup on the hot search loop; auto-detected, never required |
| GUI | pygame-ce | Desktop game + review screen |
| Tooling | uv, pytest, mypy (strict) | Reproducible env; 126 tests and a clean type check are the gates on every change |
| Deployment | lichess-bot bridge, macOS | Engine speaks UCI, so a standard bridge hosts it |
| Measurement | Stockfish (referee), fastchess (SPRT) | An engine cannot grade itself: it misjudges a position identically when playing and when grading |

**Goal.** Learn search and evaluation by building them rather than reading about
them — and then prove the result is real by making it play rated games against
strangers. A secondary goal became just as important: learn to **measure**
engine changes honestly, because most of the interesting failures in this
project were measurement failures, not coding failures.

**Scale.** ~11,600 lines total; ~6,400 in `engine/`, of which `move_finder.py`
(search, 1,651 lines) and `chess_engine.py` (rules, 1,642 lines) are the core.
Roughly a sixth of `engine/` is measurement tooling rather than chess.

**Where it stands.** ~2133 Elo, calibrated against Stockfish; playing rated
games on Lichess with every game automatically graded by an independent engine.

---

## Architecture & Algorithms

### Board representation
Board is `list[list[int]]` of small integer piece codes (`0` empty, `1-6` white,
`7-12` black). It began as `'wP'`/`'--'` strings; migrating to ints removed
string comparison from the hot loops. String codes survive only at the FEN,
SAN/UCI and GUI-image boundaries.

**Two parallel move pipelines**, which is the central design decision:

- **UI path** — `get_valid_moves()` returns rich `Move` objects; `make_move()`
  maintains move log, state log, repetition counts, full Zobrist recompute.
- **AI path** — `get_valid_moves(for_ai=True)` returns bare 5-tuples;
  `make_ai_move()` skips all logging, updates Zobrist incrementally, and returns
  a 4-tuple undo package.

The hot loop cannot afford the bookkeeping the UI needs, and the UI cannot work
without it. Keeping them separate — and converting between them at exactly one
seam (`Move.from_ai_tuple`) — is what makes both fast and correct.

### Search (`engine/move_finder.py`)
Built up incrementally, each piece measured before it was kept:

| Technique | Purpose |
|---|---|
| Negamax + alpha-beta | The base search |
| Iterative deepening | Anytime search — gives a usable move whenever the clock stops |
| Transposition table (Zobrist-keyed) | Reuse work across transpositions; depth-preferred replacement with aging |
| Quiescence search | Fixes the horizon effect — never evaluate mid-capture |
| MVV-LVA + killers + history | Move ordering; alpha-beta's benefit depends almost entirely on trying good moves first |
| Static exchange evaluation (SEE) | Prune losing captures in quiescence instead of searching them |
| Null-move pruning | Skip a turn; if still winning, the position is not worth full depth |
| PVS (null-window scouts) | Assume the first move is best, verify siblings cheaply |
| Late move reductions | Search unpromising late moves shallower, re-search if one beats alpha |
| Aspiration windows | Start each iteration in a narrow window around the last score |
| Check extension | Never stop the search inside a forced sequence |
| Futility / reverse-futility pruning | Skip nodes that cannot reach alpha |

### Evaluation
Material + piece-square tables, **tapered** between middlegame and endgame by
material phase (a king belongs in the corner at move 20 and in the centre at
move 60 — one static table cannot say both). Plus mobility, rook activity and
pawn structure terms. Always computed from White's perspective, memoized by
Zobrist key.

### Time management (`engine/uci.py`)
Soft/hard two-bound design: `time_limit` is the target, `hard_limit` funds a
panic extension when the score collapses. The budget divides the remaining clock
by an **estimate of moves still to play**, with two emergency tiers below 25s
and 10s. See the 2026-07-19 entries — this is the part with the most interesting
measurement story.

### Supporting modules
`analysis.py` — chess.com-style move grading (blunder/mistake/brilliant ladder
via win% loss). `pgn.py` — SAN import by *matching* against legal moves rather
than re-implementing rules. `uci.py`/`uci_client.py` — the engine-as-a-process
pair. `bench.py`, `abtest.py`, `selfplay.py`, `tm_replay.py`, `tm_allocate.py` —
the measurement toolkit, which grew to five modules for good reason.

---

## Timeline & Milestones

### Phase 1 — Rules engine (2026-07-01 → 07-10)
- **07-01** First commit. Board state, piece rendering, `GameState`.
- **07-04→07-06** Move generation, then legality: check detection, pin handling,
  checkmate/stalemate.
- **07-07** Castling and pawn promotion.
- **07-08** En passant, including the hidden-horizontal-pin case (capturing en
  passant removes *two* pawns from a rank, which can expose a king sideways).
- **07-10** 50-move rule, threefold repetition, GUI extracted into `gui/`.

### Phase 2 — Search engine (2026-07-16 → 07-17)
- **07-16** Zobrist hashing and the first real search (`move_finder.py`).
  Alpha-beta, move ordering, iterative deepening.
- **07-16** UCI adapter written — engine becomes hostable as a bot.
- **07-17** ✅ Perft bug found and fixed (see Challenges). Perft suite added.
- **07-17** PyPy UCI subprocess hosting; engine confirmed dependency-free.
- **07-17** Game review screen: eval bar, move grading, variations, FEN/PGN import.

### Phase 3 — Strength tuning (2026-07-18)
Ten measured stages (A–J), each committed separately so it could be reverted alone.
- ✅ Quiescence, persistent TT, extra eval terms.
- ✅ Integer piece codes. Depth-5 search **1.35s → 0.28s**.
- ✅ Eval cache by Zobrist key. Depth-6 **35% faster under PyPy**, 8% CPython.
- ✅ PVS, quiescence TT + delta pruning, log-scaled LMR, tapered eval, mobility.
- ❌ **Late move pruning — reverted.** −60 Elo, and −24 after fixing a broken test.
- ❌ **Countermove + IIR — reverted.** Never measured on its own; unproven code goes.
- **07-18** Deployed to Lichess. Self-play smoke test added.

### Phase 4 — Time management (2026-07-19)
- ✅ **Node-count methodology adopted.** Score-neutral changes verified by exact
  node equality instead of overnight matches.
- ✅ **Ray tables precomputed. 10.4% faster overall**, `_mobility` 43% faster,
  proven safe by identical node counts. Verified in ~30 seconds.
- ✅ **`tm_replay.py`** — grades the bot's 16 real online games to find where it
  actually loses.
- ✅ **Time budget reshaped** (moves-to-go + overhead reserve). Middlegame
  thinking time **+46%**.
- ❌ **Best-move stability — measured worse (1.11x vs 1.34x), reverted.**
- ❌ **400-game clock-mode gate — abandoned twice, unusable.** The absolute
  hoarding thresholds relocate when the test clock is scaled, so a 90+0 match
  measured a partially self-cancelling version of the change and read −45 Elo.
  Recorded as void rather than as evidence.

### Phase 5 — Measurement rebuilt (2026-07-19 → 07-20)
- ✅ **The 1/43 record explained.** All 47 Lichess games were *casual*, so the
  rating never left the provisional 3000 and matchmaking kept pairing the bot
  with ~2930 opponents. Not an engine result at all.
- ✅ **First real strength number: ~2133 Elo**, from three Stockfish
  `UCI_Elo` levels agreeing within 47 points (`engine/tools/calibrate.py`).
- ✅ **Bitboard rewrite considered and rejected on measurement.** python-chess
  (bitboard) benchmarks *slower* than our mailbox engine in Python.
- ✅ **UCI `info` output added** — the engine had been unobservable in
  production. It immediately exposed a depth cap bug.
- ✅ **Clock utilisation 58% → 101%.** Aborted iterations are no longer
  discarded.
- ✅ **Hoarding thresholds made relative** to the starting clock, retiring the
  trap that voided two overnight gates.
- ✅ **SPRT harness** (`engine/tools/sprt.py` over fastchess) replacing fixed-N
  matches.
- ✅ **Automated per-game Stockfish analysis** (`engine/tools/sf_watch.py`), running
  in the bot's idle time and stamped with the engine build.
- ✅ **Redeployed rated** with opponent bounds around the calibrated rating.

---

## Challenges & Solutions

### 1. The two-node perft discrepancy
**Problem.** Move generation looked correct and passed every hand-written test.

**Diagnosis.** Perft — count leaf nodes at depth N and compare to published
values. Depth 5 produced **4,865,607 nodes instead of the canonical 4,865,609**.
Two nodes wrong out of 4.8 million: a bug no amount of playing would ever
surface, and no eyeballing would ever find.

**Root cause.** `_is_square_attacked` let sliding attack rays pass *through* an
adjacent enemy pawn whenever that pawn didn't itself attack the probed square.
So a queen could "attack" a square straight through her own blocking pawn,
illegally restricting the enemy king's moves.

**Fix.** One blocking condition, plus perft(4), Kiwipete perft(1–3), and a
direct regression test. **Lesson: a test that produces one exact number beats
any number of tests that produce "looks right."**

### 2. Profiling contradicted the obvious hypothesis
**Problem.** Search was too slow. Everyone "knows" attack detection dominates a
chess engine.

**Diagnosis.** `cProfile` at depth 5 said the hotspots were `evaluate()` and
move generation — **not** `_is_square_attacked`, which is where I'd have spent
the day. A later depth-6 profile put `evaluate` at 31% of runtime, and
`_mobility` alone at 14% (121,789 calls).

**Fix.** Optimized what the profiler pointed at. The `_mobility` cost turned out
not to be its logic but its *bookkeeping*: ray-walking re-tested
`0 <= r < 8 and 0 <= c < 8` at every single step. Board geometry is fixed, so
every ray is enumerable once at import. Result: **43% faster in isolation, 10.4%
overall.**

### 3. A night of measurement that produced nothing
**Problem.** Search stages F–J ran overnight self-play matches. Net result:
**~0 Elo, most of it unresolvable noise.** A whole program of machine time
bought no knowledge.

**Diagnosis.** The instrument was wrong for the question. A 100-game match
resolves nothing finer than **±70 Elo**; 400 games gets to **±35**. Real search
improvements are worth 10–30 Elo. A 100-game verdict on a 20-Elo change is
*noise wearing a number*.

**Fix — the most valuable thing learned in this project.** Match the instrument
to the change:

- **Score-neutral changes** (faster eval, cheaper movegen): use **node count**.
  It is exactly deterministic. Identical node totals prove the search made every
  same decision, so the change *cannot* have altered play. This turns an
  overnight match into a 30-second check — and it's *stronger* evidence, because
  it's a proof rather than a statistic.
- **Behaviour changes**: self-play, 400 games minimum, never two concurrently.

The catch: node determinism only holds because the bench seeds the root RNG. The
root shuffle changes how much the search prunes, so unseeded counts don't
reproduce and the method silently stops working. That's now guarded by
`test_node_count_is_reproducible_for_a_seeded_search`.

### 4. A benchmark that couldn't see improvements
**Problem.** Self-play used a depth cap of 6 as a "safety net," assuming the
clock always stopped the search first.

**Diagnosis.** Measured it: at the 0.2s budget, **depth 6 completed naturally in
4 of 7 realistic positions**. The cap was binding, not the clock.

**Why it mattered.** A faster engine in a cap-bound position has *nowhere to
spend the speed*. Every speed optimization would measure as 0 Elo no matter how
much it really helped. The measuring instrument had a hard ceiling and was
silently reporting it as a result.

**Fix.** `DEPTH` raised to 12 — above what the budget can reach, leaving the
clock as the only binding constraint.

### 5. The engine was playing a faster time control than it was given
**Problem.** Bot was blundering in positions where it had time to spare.

**Diagnosis.** Built `tm_replay.py` to replay all 16 real online games, parse
`%clk` annotations for actual time spent, and grade every move. Two findings:

- It finished **sixty-move games with 28–48% of its clock unspent.**
- **73% of its blunders/mistakes/missed wins fell in moves 21–40** — 8% and 7%
  error rates, against 1% in moves 1–20 where the opening book answers anyway.

**Root cause.** `clock_move_budget` divided the remaining clock by a constant 30.
Dividing by a constant **decays geometrically**, so the clock is never actually
spent — it just asymptotes. Time was being hoarded forever and left on the table
at the end.

**Fix.** Divide by an *estimate of moves remaining* instead. Middlegame budget:

| Time control | Moves 21–40, before | After | Change |
|---|---|---|---|
| 5+0 | 3.7s | 5.4s | **+46%** |
| 10+2 | 9.3s | 12.9s | **+39%** |

Opening damped (5+0 move 1: 10.0s → 5.7s), unspent clock after a 45-move game
cut from ~25% to 18%.

**Near-miss worth recording.** The first draft engaged its safety guard below
60s — which in a 5+0 game means hoarding from about move 35, *precisely inside
the band where the errors happen*. A simulation caught it flagging at move 77.
The shipped version uses 25s/10s tiers and survives a marathon identically to
the old rule (both reach move ~124).

### 6. The heuristic that measured backwards — twice
**Problem.** The headline feature: "know when to think longer." Standard idea —
if the root best move keeps changing between iterations, the position is sharp,
so extend.

**Diagnosis, attempt 1.** Measured a uniform 1.01x. The mechanism **wasn't
firing at all**: I counted every best-move change, but depths 1–3 always flip,
so the signal was drowned. Fixed with a decaying accumulator.

**Diagnosis, attempt 2.** Now it fired — *backwards*, at 0.64x. I traced it
per-iteration and found the fault was in **my test labels, not the code**. I had
hand-picked Kiwipete as "hard" and a rook endgame as "easy." The engine picks
the same move at every depth in Kiwipete (calm) and flaps all search long in the
endgame (unstable). **My labels were exactly inverted.**

**Resolution.** Rebuilt the instrument (`tm_allocate.py`) on *real blunders from
real games* — the only labels not contaminated by my guesses. Against those:

| Rule | Critical/routine time ratio |
|---|---|
| Existing panic rule (binary score-drop) | **1.34x** |
| + continuous response + stability signal | 1.11x |
| + continuous response only | 1.36x (a wash) |

**The planned feature was worse than the crude rule it was meant to replace.**
Reverted; only the negative result is committed, as a comment where the next
person will look.

**Why stability backfires here:** in quiet endgames the root best move flaps
between moves of *identical* score, so it reads as maximally unstable exactly
where there's least to think about.

**The part that matters most:** a self-play match would have called this whole
change "neutral" after a full night, with **no way to distinguish "didn't work"
from "never fired."** Both failure modes were caught in minutes by an instrument
that observed the mechanism directly.

### 7. Production incident — HTTP 429 lockout
**Problem.** Bot appeared online but accepted challenges without ever playing.
Error log full of `RateLimitedError`.

**Diagnosis.** Lichess rate-limits `/api/stream/event`. Restarting the bot
repeatedly while iterating on config tripped a 429 the bridge couldn't recover
from — it retried *faster* than the ~60s cooldown, so it could never escape. Made
worse by orphaned `multiprocessing` children from previous runs holding streams
open, invisible to a normal process check.

**Fix.** Sweep orphans explicitly (`ps aux | grep "lichess-bot/.venv"`), then one
clean start and wait in silence. Operational rule adopted: **batch all config
edits, then exactly one restart cycle.**

---

### 8. Losing 42 of 43 games, and why that was a config bug

The bot went 1/43 against Lichess opponents averaging 2928. Read at face value
that is a damning number, and it triggered a full "the engine is
over-engineered, rebuild it from scratch" review — including a proposal to
migrate to bitboards.

The API told a different story:

```
"count": { "all": 47, "rated": 0, "win": 5, "loss": 42 }
"perfs": { "blitz": { "games": 0, "rating": 3000, "rd": 500, "prov": true } }
```

**`rated: 0`.** Every game had been played casual, so the rating never moved off
Lichess's provisional 3000 — and matchmaking used that 3000 to choose
opponents. It is a closed loop with no exit: casual games → rating never
updates → stays 3000 → paired with ~2900 bots → lose everything → still no
rating feedback.

The setting was mine, chosen as a safety measure when first deploying. I never
asked what it did to the *measurement*.

Two independent instruments then showed the engine was not the problem.
Stockfish at depth 14 over all 43 games put our blunder rate at **0.4%** (5 in
1289 moves) — not an engine that hangs pieces, an engine consistently slightly
worse than a much stronger opponent. And a calibration ladder against
`UCI_LimitStrength` Stockfish returned:

| Stockfish level | Our score | Implied rating |
| --- | --- | --- |
| 1600 | 19.0/20 | ~2112 |
| 2000 | 13.5/20 | ~2127 |
| 2400 | 4.0/20 | ~2159 |

Three levels agreeing within 47 Elo. Against 2928-rated opposition, a 2133
engine is *expected* to score 0.44 points in 43 games. It scored 1.0 — better
than its rating predicted.

**What it cost:** the entire time-management program had been tuned against
those games. Not wrong work, but work whose evidence base could not support any
conclusion — an 1800 engine and a 2400 engine both score ~0% at that gap, so
the sample was structurally incapable of distinguishing them.

**The general lesson, which is the one worth keeping:** I had built three
increasingly sophisticated analysis tools on top of a dataset without ever
checking that the dataset could express the thing being measured. Sophistication
downstream cannot recover a signal that was never sampled. The fix — one config
line, `challenge_mode: rated` — was trivial. Finding it took reading the raw
API response instead of the scoreboard.

### 9. Rejecting the bitboard rewrite on evidence

The natural next move after "the engine is too complex for its performance" was
to rewrite the board representation as bitboards — the standard answer in
chess programming, and the one every reference recommends.

Measured first, same perft, make/unmake at every ply:

| Engine | Representation | Host | Nodes/sec |
| --- | --- | --- | --- |
| python-chess | **bitboard** | CPython | 335,510 |
| PyCheckmate | mailbox + piece-sets | CPython | **392,994** |
| PyCheckmate | mailbox + piece-sets | PyPy | **958,410** |

Our mailbox engine is already 17% faster than a mature bitboard library on
CPython. The reason is that the bitboard advantage is a *C* advantage:
`attacks & ~own` is one machine instruction over 64 squares, but in Python every
`&` allocates a heap integer, and Python has no `uint64` — bit 63 silently
promotes into arbitrary-precision arithmetic. Meanwhile mailbox with piece-sets
iterates only the ~16 pieces that exist.

Profiling closed the argument: move generation is **37%** of search time, so by
Amdahl's law even an infinitely fast generator caps out at a 1.6× speedup —
roughly half a ply. Weeks of work on the most correctness-critical code in the
project, in a direction the benchmark said was slower.

**The instinct behind the request was right, the mechanism wasn't.** The real
levers for depth are searching *fewer* nodes (ordering) and incremental
evaluation, not visiting nodes faster.

### 10. An engine nobody could see

The UCI adapter emitted no `info` lines at all — no depth, no score, no nodes.
Lichess showed nothing, and no record existed of what depth a real game reached.
Every diagnostic in this document had to be reconstructed offline from finished
PGNs, which cannot see what the engine actually thought at the time.

Adding them took an hour, and the first run exposed a bug they would have caught
weeks earlier: `go movetime 4000` returned after **452ms**, having stopped at
depth 5. The clock path set an unlimited depth; the `movetime` path fell through
to `DEFAULT_DEPTH = 5`. Lichess was unaffected (the bridge sends `wtime`/`btime`),
but every movetime-driven test had been measuring a depth-capped engine.

Fixing it changed the move played on the test position.

**Observability is not a nice-to-have on a system you are trying to measure.**
The whole project had been running instruments against a black box.

### 11. A third of the clock, thrown away

With `info` lines available, the search turned out to be spending 58% of a 3s
budget and 69% of a 5s one.

Two rules combined to cause it. `_search_root` discarded a timed-out iteration
*wholesale*, and because starting an iteration you couldn't finish was therefore
pure waste, the soft-stop gate refused to begin one past 45% of the budget. The
second rule was a rational response to the first.

The first is the real defect. Root moves are ordered best-first from the
previous iteration, so a partial pass has already examined the most promising
candidates — it either confirms the previous best or replaces it with a move
that outscored it a ply deeper. Both are strictly better information than
stopping early. Keeping the partial result let the gate move to 0.9:

| Budget | Before | After |
| --- | --- | --- |
| 3.0s | 1.73s (58%) | 3.03s (101%) |
| 5.0s | 3.44s (69%) | 5.01s (100%) |

That then exposed a second-order problem: the clock was sampled every 2048
nodes (~58ms), an error that is near-constant in *time* and therefore only
bites when the budget is small — a 0.1s budget overran by 41%, exactly the
situation a nearly-flagged clock produces. Sampling every 512 nodes quartered
it for 0.6% on the benchmark, **with the node count unchanged** — which is the
proof that the search itself was untouched.

### A negative result expired without anyone noticing (2026-07-21)

Best-move stability — narrowing the time gate when successive search
iterations keep returning the same root move — was built, measured, and
deleted months earlier. It scored 1.11x against the crude panic rule's 1.34x
on the question "does the engine think longer on positions it actually
blundered". A comment was left in front of `PANIC_SCORE_DROP` recording both
the verdict and the reason:

> in quiet endgames the root best move flaps between moves of *identical*
> score, so it reads as maximally unstable exactly where there is least to
> think about

When the Phase 1 plan called for building it again, that comment was the
reason to *check* rather than the reason to skip. The check said the premise
was gone. Ties can no longer displace the incumbent: `_search_root` compares
with a strict `score > best_score` and searches the previous iteration's best
move first, so an equal-scoring rival is examined second and loses. Measured
over 12 positions and 84 iteration transitions:

| positions | best-move change rate | equal-score changes |
| --- | --- | --- |
| quiet | 2% | **0** |
| sharp | 24% | **0** |

The flapping the note describes is now literally zero, and the signal
discriminates 12x in the direction it was supposed to. Nobody set out to fix
it — it fell out of an unrelated change that reordered root moves best-first
so aborted iterations could be kept.

Rebuilt on that basis, the gate now slides from 0.9 down to 0.5 of the budget,
one step per consecutive stable iteration, with panic overriding it outright.
Share of a 3s budget actually spent:

| | off | on |
| --- | --- | --- |
| quiet | 100.3% | **71.9%** |
| sharp | 100.4% | 95.5% |

It takes clock from positions with nothing left to find and leaves sharp ones
alone — which is the behavior the first attempt was aiming at and got
backwards.

**The transferable lesson is about the shape of the record, not about chess.**
A measured negative result is only valid while the code it measured still
exists, and nothing announces when that stops being true. What made the
expiry detectable was that the note recorded the *mechanism* rather than the
verdict. Had it said "tried best-move stability, measured worse, don't
rebuild", skipping would have been correct on the evidence available and wrong
in fact — and the error would have been invisible, because a thing not built
produces no symptom.

One caveat kept deliberately: this measures **clock reallocation, not Elo**.
The original experiment's 1.36x-vs-1.34x result is a fair warning that the
signal working does not mean the change is worth anything. Returning time to
the endgame only pays if the endgame spends it well, and only the rated-games
run can say.

### Staged move ordering: built, measured, reverted (2026-07-21)

`docs/ENGINE_V2_PLAN.md` §5.3 lists staged generation — "yield TT move → captures →
killers → quiets, so a cutoff on the TT move never pays for generating quiets
at all… a node-count win *and* a time win". Instrumenting the search first
turned that from a slogan into numbers, over 13,534 generating nodes:

| | share of generating nodes |
| --- | --- |
| any beta cutoff | 71.0% |
| cutoff on the first move tried | 62.8% |
| cutoff on the **TT move** specifically | 18.2% |
| **cutoff before any quiet was needed** | **46.0%** |
| quiet share of generated moves | 93.3% |

The TT-move framing undersells it by 2.5x — only 25% of nodes have a TT move
at all, and most first-move cutoffs come from MVV-LVA and killers rather than
the table.

**Two measurements then redirected the work, and a third killed it.**

*Staged generation is worth little here.* A noisy-only pass costs **78-80%** of
a full pass in typical positions, because the generator walks every piece and
every ray either way — `captures_only` merely declines to append. So skipping
quiets saves ~20% of generation, and `0.46 x 0.20 x 0.37 ≈ 3%` of search time,
before paying for a second stage on the other 54% of nodes.

*Ordering looked like the better target.* Scoring and sorting costs **35-41%**
of combined generate-and-order time, and unlike generation it is entirely
skippable for moves never searched. Estimated ~9%. So the build became staged
*ordering*: partition by a board lookup per move, score and sort only what
outranks a killer, and score the remaining ~93% lazily.

*It measured neutral.* Best-of-5 back to back, CPython 3.29-3.33s against a
3.27-3.59s baseline; PyPy 2.59-2.68s against 2.60-2.88s. Overlapping ranges on
both interpreters, so by this project's own rule there is nothing to believe.
Reverted.

**Why the 9% did not appear** — the part worth keeping, because it tells the
next attempt what to fix. The saving lands on 46% of nodes, but the machinery
is paid on 100% of them: a generator's per-yield cost, a partition pass over
every move, and a scorer promoted from a closure to a six-argument module
function so both paths could share one definition. Python call overhead on
every node cancelled the scoring skipped on some of them. A version that keeps
the closure and avoids the generator might still win; this one had its cost in
exactly the place its benefit was supposed to come from.

**One sub-result is solid and worth reusing.** Staged *yielding* with eager
scoring reproduces the old order exactly — 59,410 nodes against 59,410 — which
proves the band partition is sound (first stage >= 900,000, losing captures
held back below killers at `300_000 + SEE`). The node count only moved once
scoring went lazy, to 59,092: stage-2 quiets get scored against a history table
that stage-1 subtrees have already updated. Fresher information, 0.5% fewer
nodes, and a reminder that lazy scoring is not automatically behavior-neutral
even when lazy *ordering* is.

### Texel tuning: the tool works, the dataset does not (2026-07-22)

Mistake M7 is that ~34 evaluation constants were hand-picked and never fitted,
and §7.5 put Texel tuning at 50-100 Elo — the highest-value strength item never
attempted. `engine/tools/tune.py` now implements it: replay the bot's PGNs into
positions labeled by game result, fit the sigmoid constant K, then coordinate
descent on the weights against mean squared error.

Two safeguards were built in from the start rather than added after a bad
result, and they are the reason this entry exists:

- **Split by game, not by position.** Positions within one game share pawn
  structure, material and result, so a position-level split leaks the answer
  across it.
- **Keep only what improves error on games the fit never saw.** Training error
  always falls; that is what fitting means.

**Run to convergence, it overfits outright.** Training error fell 33% (0.0871
-> 0.0587) while held-out error bottomed at round 3 and then climbed to 0.1401
— worse than not tuning at all. The converged weights were chess nonsense:
bishop below knight, knight outposts penalized, isolated pawns rewarded, every
passed-pawn bonus negative.

**Early stopping rescued a plausible-looking 5.9% held-out improvement — and
that turned out to be noise too.** Repeating the entire fit across four
different game splits:

| split | start valid | best valid | change |
| --- | --- | --- | --- |
| 1 | 0.0938 | 0.0909 | -3.1% |
| 2 | 0.1089 | 0.1022 | -6.2% |
| 3 | 0.1285 | 0.1285 | **+0.0%** |
| 4 | 0.0661 | 0.0543 | **-17.8%** |

The answer ranges from nothing to 18% depending only on which 32 games are held
out, and the *starting* error varies 2x across the same splits. The 5.9% was one
draw from that distribution.

The cause is volume: 127 usable games give **3,231 quiet positions**, about 111
per parameter, where the method wants thousands. **The tuned values were not
applied.**

This is the ±70-Elo-instrument mistake in a new costume — a number that looks
like a result because it came out of an optimizer, on a sample too small to
carry it. The difference this time is that the check was built before the
number existed, so it cost one afternoon instead of a night of matches. The
tool is kept and re-runs cheaply; the gate for believing it is that the
split-to-split spread above collapses.

---

## Performance Benchmarks

Measured with `engine.tools.bench` (4 positions: opening / middlegame / tactical /
endgame), seeded RNG, best-of-5 back-to-back runs.

| Date | Change | Metric | Before | After | Gain |
|---|---|---|---|---|---|
| 07-18 | Integer piece codes + search cuts | Depth-5 search | 1.35s | 0.28s | **~4.8x** |
| 07-18 | Zobrist eval cache (PyPy) | Depth-6 search | — | — | **35% faster** |
| 07-18 | Zobrist eval cache (CPython) | Depth-6 search | — | — | 8% faster |
| 07-19 | Precomputed ray tables | Bench total (best-of-5) | 3.471s | 3.111s | **10.4%** |
| 07-19 | Precomputed ray tables | `_mobility` isolated (56k calls) | 0.053s | 0.030s | **43%** |

**Safety proof for the ray-table change:** both versions visited **113,218 nodes
— exactly**. Identical node counts across all four positions mean every search
decision was unchanged, so the optimization provably cannot have altered play.
Sample ranges were disjoint (3.471–3.499 vs 3.111–3.210), so the timing result
is real and not the ~29% run-to-run noise this machine shows.

**Time allocation** (`tm_replay` over 16 real games):

| Metric | Before | After |
|---|---|---|
| Moves 21–40 budget, 5+0 | 3.7s | 5.4s (+46%) |
| Moves 21–40 budget, 10+2 | 9.3s | 12.9s (+39%) |
| Clock unspent after 45-move game | ~25% | 18% |
| Opening move 1 budget, 5+0 | 10.0s | 5.7s (damped) |

_Not yet measured: nodes/second, and depth-reached-in-fixed-time over the project's
history. Both would strengthen this table; neither was recorded early enough to
reconstruct honestly._

---

### Representation: mailbox vs bitboard (2026-07-19)

Perft from the start position, make/unmake at every ply:

| Engine | Representation | Host | Nodes/sec |
| --- | --- | --- | --- |
| python-chess | bitboard | CPython | 335,510 |
| PyCheckmate | mailbox + piece-sets | CPython | **392,994** |
| PyCheckmate | mailbox + piece-sets | PyPy | **958,410** |

### Where search time goes (`cProfile`, depth 6, middlegame)

| Component | Share |
| --- | --- |
| Move generation | 37% |
| Evaluation | 22% |
| Ordering, SEE, make/unmake, overhead | 41% |

### Clock utilisation (2026-07-20)

| Budget | Before | After |
| --- | --- | --- |
| 3.0s | 1.73s (58%) | 3.03s (101%) |
| 5.0s | 3.44s (69%) | 5.01s (100%) |

Overrun at a 0.1s budget: 141% → 111% (clock sampled every 512 nodes instead
of 2048; benchmark cost 0.6%, node count unchanged).

### Move quality by game phase (Stockfish depth 12, 43 games, our moves)

| Moves | ACPL | Blunders |
| --- | --- | --- |
| 1–15 | 13.3 | 1 |
| 16–30 | **30.2** | 5 |
| 31–45 | 28.1 | 3 |
| 46+ | 24.1 | 1 |

The opening is clean — the polyglot book is doing its job. Play degrades
sharply the moment it runs out while the position is still complex.

---

## Testing & Validation

**126 tests, plus strict mypy across 34 source files.** Both gate every change.

| Method | What it proves | Result |
|---|---|---|
| **Perft** (depths 1–4: 20 / 400 / 8,902 / 197,281, plus Kiwipete) | Move generation is exactly correct | Byte-exact. Caught the 2-node ray bug at depth 5 |
| **Random-walk make/unmake** | `white_pieces`/`black_pieces` sets stay exact through thousands of random moves | Passing — guards the subtlest class of bug in the codebase |
| **Node-count reproducibility** | The measurement method itself still works | Passing — protects the seeded-RNG invariant |
| **Self-play smoke test** | Whole games hold together over the real UCI round-trip; referee rejects illegal moves | Passing |
| **A/B self-play matches** (`abtest.py`) | Strength changes, 400 games ≈ ±35 Elo | Used to reject LMP (−60/−24 Elo) |
| **Blunder replay** (`tm_replay.py`) | Where the engine actually loses, from real games | 73% of errors in moves 21–40 |
| **Allocation probe** (`tm_allocate.py`) | Whether a time rule spends more where games were lost | Rejected the stability heuristic (1.11x vs 1.34x) |
| **Live Lichess play** | Everything, against real opponents | Deployed and playing |

**Measurement instruments** (the part that took longest to get right):

| Instrument | Question it answers |
| --- | --- |
| `engine/tools/bench.py` | Did a score-neutral change stay score-neutral? *Node counts must be identical* — a proof, not a p-value |
| `engine/tools/sprt.py` | Is version B stronger than version A? Sequential test over fastchess, stops when decisive, declines when neutral |
| `engine/tools/calibrate.py` | How strong are we in absolute terms? Stockfish pinned by `UCI_LimitStrength` |
| `engine/tools/sf_review.py` | Where did we go wrong in these games? Independent Stockfish grading |
| `engine/tools/sf_watch.py` | Same, automatically, in the bot's idle time, stamped with the engine build |
| perft suite | Is move generation still exactly correct? |

Two design rules were learned the hard way and now govern all of them: **one
change in flight at a time** (five features landing together with a net zero
teaches nothing about any of them), and **never average across engine
versions** (a mean over two engines describes neither).

**Known limitations.**
- No opening book of its own (relies on the bridge's polyglot book).
- No endgame tablebases.
- Single-threaded search — no SMP.
- Evaluation has no king-safety term yet.
- 34 hand-picked search/evaluation constants, none ever fitted to data.
- Self-play at a scaled clock is no longer used as a verdict at all; see the
  clock-fidelity trap. The historical caveat below applies to the abandoned
  gate: it used self-play games averaging 72 moves per side, while real
  online games average 45 — so it exercises the time schedule's tail harder than
  deployment does.
- Zero flagged games observed so far in the gate, meaning the *overspend
  protection* half of time management remains untested by self-play.

---

## Results & Current Status

**Deployed on Lichess as `PyCheckmate`** (blitz 5+0 and rapid 10+2; bullet
disabled — move overhead is too tight to be safe there yet), playing **rated**
games against opponents bracketed to 1800–2500.

**Strength: ~2133 Elo** (2026-07-19), measured against Stockfish pinned to
requested ratings via `UCI_LimitStrength`:

| Stockfish level | Score | Implied rating |
| --- | --- | --- |
| 1600 | 19.0/20 (95.0%) | ~2112 |
| 2000 | 13.5/20 (67.5%) | ~2127 |
| 2400 | 4.0/20 (20.0%) | ~2159 |

Three independent levels agreeing within 47 Elo — the mutual consistency is
stronger evidence than any single level's error bar, since the model is
answering the same question from very different score ratios.

**Quality, graded by Stockfish** over 43 games (depth 14): ACPL 19.5, blunder
rate 0.4% (5 in 1289 moves). For scale, the ~2930-rated opponents in that set
averaged 6.8 ACPL.

> **📌 Lichess rating — pending.** The first 47 games were casual, so no rated
> rating exists yet. The bot was redeployed rated on 2026-07-20; a provisional
> figure needs ~10 games and a stable one a few dozen. Fill in once available:
> - Blitz / Rapid rating: `________`
> - Rated games played / W-L-D: `________`
> - Profile: https://lichess.org/@/PyCheckmate

**What's left**
- Texel tuning of the 34 hand-picked search/eval constants — none has ever been
  fitted to data. The largest single known gain (~50–100 Elo typical).
- King safety in the evaluation (largest known eval gap; half the blunders fall
  in moves 16–30, right where the opening book runs out).
- Incremental evaluation, updating material/PST deltas inside make/unmake
  instead of rescanning — removes the 22% evaluation slice at its root.
- Collapsing the dual move pipeline (`for_ai` + `make_ai_move` alongside `Move`
  + `make_move`; 64 references, every invariant maintained twice).
- Opening book owned by the engine rather than the bridge.

**What "done" looks like.** The engine plays rated blitz unattended without
crashing, flagging, or hanging pieces to the horizon effect, at a stable rating
I can actually quote. The engineering side of that is met, and the strength
number now exists (~2133); what remains is letting the rated deployment produce
a public Lichess rating to match it.

---

## Key Takeaways

1. **The hardest problem was measurement, not chess.** Writing alpha-beta is a
   weekend. Knowing whether your change *helped* is the actual discipline. I
   burned a full night of self-play on search stages F–J for ~0 Elo of
   unresolvable noise, because I was asking a ±70-Elo instrument about a 20-Elo
   change.

2. **Deterministic checks beat statistical ones whenever you can get them.**
   Node count is exact: identical totals *prove* a change didn't alter play. That
   replaced an overnight match with a 30-second check for the ray-table
   optimization — and it's stronger evidence, since it's a proof rather than a
   p-value. The lesson generalizes past chess: prefer the check that can only
   have one answer.

3. **My intuitions about my own code were wrong more often than they were
   right.** I expected attack detection to be the hotspot; it was evaluation. I
   expected best-move stability to identify hard positions; at the time it
   identified quiet endgames flapping between equal moves (it does now identify
   hard positions — see takeaway 4). I hand-labelled two test positions and
   got both *backwards*. Every one of those was caught by measuring, and none of
   them would have been caught by reading the code.

4. **Negative results are worth committing — and they expire.** Three features
   were built, measured, and deleted — LMP (−60 Elo), countermove+IIR
   (unproven), best-move stability (1.11x vs the crude rule's 1.34x). Each left
   behind a comment explaining *why* it failed, and that "why" is the load-
   bearing part. In 2026-07 best-move stability was rebuilt and worked, because
   an unrelated change had removed the exact mechanism the old comment blamed —
   and the comment naming that mechanism is the only reason anyone noticed. A
   note saying "tried it, measured worse, don't rebuild" would have been
   obeyed, correctly on the evidence and wrongly in fact. **A negative result
   is a statement about code that existed at the time, not a law**, and a thing
   not built produces no symptom to alert you.

5. **Check that your data can answer your question before analysing it.** I
   built three increasingly sophisticated analysis tools on top of 43 games
   that were structurally incapable of measuring anything — every one played
   casual, so matchmaking paired a 2133 engine against 2928 opponents, where an
   1800 and a 2400 engine both score ~0%. No amount of downstream
   sophistication recovers a signal that was never sampled. The fix was one
   config line; finding it meant reading the raw API response instead of the
   scoreboard.

6. **"Rewrite it in the faster representation" is a hypothesis, not a plan.**
   The obvious fix for a slow Python engine is bitboards. Measured, our mailbox
   engine is *17% faster than python-chess's bitboards* on CPython — because
   the bitboard win is a C win, and in Python every bitwise op allocates.
   Profiling then capped the entire theoretical gain at 1.6× anyway. The
   instinct behind the request was right; the mechanism would have made things
   worse.

7. **You cannot debug what you cannot see.** The engine shipped without a
   single UCI `info` line. Adding them took an hour and immediately surfaced a
   bug that had been silently capping every movetime-based test at depth 5 —
   and then a second one, where the search was discarding a third of its clock.
   Both had been invisible for weeks while I ran ever-more-elaborate offline
   analysis against a black box.

8. **The bug that mattered was invisible to playing the game.** Two nodes wrong
   out of 4.8 million — undetectable by watching games, unhittable by
   hand-written tests, found instantly by one number that had a known correct
   value. Since then, every subsystem got an exact-value test if one existed.

---

_Last updated: 2026-07-20 — engine calibrated at ~2133 Elo; redeployed rated
with automated per-game Stockfish analysis running alongside the bot._
