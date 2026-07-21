# Engine v2 — retrospective and rebuild plan

Written 2026-07-19, after the first engine's 43-game Lichess run ended 1/43.

This document is in three parts: **what the measurements actually say**, **what
I got wrong building v1**, and **the plan for v2**. The first part has to come
first, because the headline number that triggered this rebuild turns out to
mean something different from what it looks like.

---

## Part 1 — What the measurements say

### 1.1 The 1/43 record is not an engine measurement

`GET /api/user/PyCheckmate` returns:

```
"count": { "all": 47, "rated": 0, "win": 5, "loss": 42, "draw": 0 }
"perfs": { "blitz": { "games": 0, "rating": 3000, "rd": 500, "prov": true }, ... }
```

**Every game was casual.** `rated: 0`, and `games: 0` in every time control. So
the rating never moved off Lichess's provisional 3000 with RD 500 — and
matchmaking used that 3000 to choose opponents. Average opponent Elo across the
43 games: **2928**.

This is a closed loop that cannot ever open by itself:

```
casual games -> rating never updates -> stays 3000 -> matchmaking picks ~2900 bots
                                                   -> we lose ~everything
                                                   -> (no rating feedback)
```

An 1800 engine and a 2400 engine both score ~0% against 2900 opposition. The
sample therefore **cannot distinguish them**, and no analysis downstream can
recover a signal that was never in the data. Every conclusion drawn from these
games — including the entire time-management program — rests on that sample.

`challenge_mode: casual` was my setting, chosen for safety. I never asked what
it did to the measurement. That is mistake #1 and it is the parent of most
of the others.

### 1.2 Stockfish says the engine is not actually blundering

Full depth-14 Stockfish pass over all 43 games (`engine/tools/sf_review.py`):

| Set | ACPL | Inaccuracies | Mistakes | Blunders | Moves |
| --- | --- | --- | --- | --- | --- |
| us, old TM (16 games) | 23.2 | 52 | 29 | 6 | 674 |
| us, new TM (27 games) | **19.5** | 96 | 44 | 5 | 1289 |
| opponents (all 43) | 6.8 | 36 | 19 | 2 | 1989 |

Blunder rate is **0.4%** (5 in 1289). This is not an engine that hangs pieces.
It is an engine that is consistently, quietly slightly worse than a 2900
opponent on almost every move — which is exactly what a ~700 Elo gap looks
like. The failure mode is *depth*, not correctness.

(Incidental: the new time management does look better, 23.2 → 19.5 ACPL. I am
not claiming that as a verdict — different opponents, different games,
uncontrolled. It is a hint, not a result.)

### 1.3 Calibration against a known ruler

Since the Lichess sample is uninformative, I built `engine/tools/calibrate.py`:
Stockfish with `UCI_LimitStrength` + `UCI_Elo` is a calibrated opponent, so
bracketing the level where we score 50% gives a real number in an hour.

Results are in §1.6 (the ladder was still running as this was written; the
first levels are decisive on their own).

### 1.4 Speed: where the time actually goes

Perft, start position, same make/unmake work at every ply:

| Engine | Representation | Host | Nodes/sec |
| --- | --- | --- | --- |
| python-chess | **bitboard** | CPython | 335,510 |
| PyCheckmate | mailbox + piece-sets | CPython | 392,994 |
| PyCheckmate | mailbox + piece-sets | PyPy | **958,410** |

We are already **17% faster than a mature bitboard engine on CPython, and 2.9×
faster on PyPy.** This matters a lot for Part 3 and I will come back to it.

Search runs at ~35k nps against ~393k nps for movegen alone. `cProfile` on a
real middlegame position, depth 6:

| Component | Share of search time |
| --- | --- |
| move generation (`_get_all_possible_moves` cum.) | 37% |
| `evaluate` (cum.) | 22% |
| ordering, SEE, make/unmake, search overhead | 41% |

### 1.5 Complexity inventory

| Metric | Value |
| --- | --- |
| `engine/move_finder.py` | 1,651 lines |
| `engine/chess_engine.py` | 1,642 lines |
| `_negamax` alone | 218 lines |
| Hand-picked tuning constants in `move_finder.py` | **34** |
| Constants ever fitted to data | **0** |
| References to the `for_ai` dual pipeline across the repo | 64 |

### 1.6 Calibration ladder result — the engine is ~2130 Elo

20 games per level, 0.5s/move, colour-reversed opening pairs:

| Stockfish level | Our score | Implied rating |
| --- | --- | --- |
| 1600 | 19.0/20 (95.0%) | ~2112 |
| 2000 | 13.5/20 (67.5%) | ~2127 |
| 2400 | 4.0/20 (20.0%) | ~2159 |

**Three independent levels agree within 47 Elo.** That mutual consistency is
far stronger evidence than any single level's error bar — the logistic model
is being asked the same question from three very different score ratios and
returns the same answer each time. Pooled estimate: **~2133 Elo.**

Now put that back against the Lichess record:

| | |
| --- | --- |
| Our strength | ~2133 |
| Average opponent | 2928 |
| Gap | 795 Elo |
| **Expected score over 43 games** | **0.44 points** |
| **Actual score** | **1.0 points** |

**We outscored expectation by more than 2×.** The 1/43 record that prompted
this rebuild is not evidence of a broken engine — it is very close to the exact
arithmetic consequence of pairing a 2130 engine against 2930 opposition. The
engine performed *better* than its rating predicted.

This inverts the conclusion of this document. See Part 6.

---

## Part 2 — Mistakes I made building v1

Ordered by how much they cost.

### M1. Never established a baseline rating — and built instruments on top of the gap

The whole point of a measurement program is to detect change. I built three
increasingly sophisticated instruments (`tm_replay`, `abtest`, `sf_review`)
without ever checking that the underlying sample could express the thing being
measured. It could not. Games lost by 700 Elo are dominated by the gap, not by
whichever 20-Elo change I made that morning.

**Next time:** the *first* engine milestone, before any optimisation, is a
number: "we are X Elo, ±Y". Nothing gets tuned before that exists.

### M2. Optimised time management before strength

Time management is worth maybe 20–40 Elo, and only once the engine's moves are
good enough that having more time to find them matters. I spent the largest
single block of the project on it while a ~700 Elo search/eval deficit sat
untouched. Correct order: **correctness → depth → evaluation → time management.**

### M3. Used fixed-N matches where SPRT was required

I judged 5–20 Elo changes with 100–400 game matches whose resolution is ±70 /
±35 Elo. That is structurally underpowered — it is why stages F–J returned
"~0 net Elo, most of it unresolvable noise". The field solved this: **SPRT**
stops as soon as the evidence is decisive, typically needing far fewer games
for a clear result and refusing to answer when the change is genuinely neutral.
([chessprogramming](https://www.chessprogramming.org/Sequential_Probability_Ratio_Test),
[dogeystamp pt.3](https://www.dogeystamp.com/chess3/),
[engine-testing-guide](https://dannyhammer.github.io/engine-testing-guide/sprt.html))

**Next time:** `fastchess` with `-sprt elo0=0 elo1=5 alpha=0.05 beta=0.05`, and
no feature is merged without a passing SPRT.

### M4. Bundled features, then reverted them in bundles

Stages F–J added PVS, quiescence TT + delta pruning, LMP, an LMR table, and
countermove+IIR. Some were measured only in combination; `cceaa22` reverts
countermove+IIR with the commit message "never measured on its own". When five
changes go in and the total is zero, you have learned nothing about any of the
five — two could be +30 and three −20.

**Next time:** one feature, one SPRT, one commit. Never a second change in
flight.

### M5. Trusted self-play at a scaled clock

`LOW_CLOCK_SECONDS` (25s) and `PANIC_CLOCK_SECONDS` (10s) are absolute, so a
90+0 test clock relocates them from move 60 to move 39 — inside the exact band
the change targeted. The match measured a partially self-cancelling version of
the code and read −45 Elo. This is documented in `CLAUDE.md` and it cost two
full overnight gates.

**Next time:** any threshold used in tuning is expressed as a *fraction of the
game*, not an absolute second count — or the test runs at the deployed control.

### M6. Two parallel move pipelines

`get_valid_moves(for_ai=True)` → 5-tuples → `make_ai_move`/`unmake_ai_move`,
alongside `Move` objects → `make_move`/`unmake_move`. 64 references across the
repo. Consequences:

- Every make/unmake invariant has to be maintained twice (the `white_pieces` /
  `black_pieces` sets are the known trap, guarded by a random-walk test that
  exists *because* this bug already happened).
- Perft only exercises the AI path, so the UI path is less tested.
- `Move.from_ai_tuple` / `to_ai_tuple` exist purely to bridge a split that
  didn't need to happen.

The user is right that this is unforced complexity. It was a real speed win at
the time, but it should have been solved by making *one* representation fast
rather than by keeping two.

### M7. 34 hand-picked constants, zero fitted

`RFP_MARGIN = 120`, `FUTILITY_MARGIN = (0, 150, 300)`, `NULL_MOVE_REDUCTION = 2`,
the LMR table, the aspiration deltas, every piece-square table value. All
guessed, all plausible, none measured. Texel tuning the evaluation against real
game outcomes is a standard, well-documented ~50–100 Elo win and I never
attempted it.

### M8. Evaluation cache before evaluation quality

I added a 1M-entry Zobrist-keyed eval cache (`8234017`) — an optimisation of a
function that was still missing king safety, threats, and passed-pawn king
distance. Caching a weak evaluation faster does not make it stronger.

### M9. Fixed the symptom in the analysis tool, twice

`sf_review` initially reported ACPL 72.6 where Lichess said 49, because I
clamped per-move loss but not the evaluation. Caught only because I checked
against Lichess's own published numbers on the same games. Had I not had an
external reference, I would have shipped inflated numbers and reasoned from
them.

**Next time:** every analysis tool gets validated against a known-correct
external reference before its output is used for a decision.

---

## Part 3 — On bitboards: I think this one is wrong, and here is the evidence

The proposal was: implement bitboards and "you won't almost ever worry about
the speed of our engine again." I want to argue against it, because the
measurements point the other way.

### 3.1 We are already faster than a bitboard engine

`python-chess` is the reference bitboard implementation in Python — mature,
heavily optimised, widely used. Measured above, same perft, same make/unmake at
every ply: **python-chess 335k nps, us 393k nps on CPython, 958k nps on PyPy.**

### 3.2 Why bitboards don't transfer to Python

The bitboard advantage in C is that `attacks & ~own_pieces` is *one machine
instruction* covering 64 squares. In Python:

- Every `&`, `|`, `<<` allocates a new heap-allocated `int` object and goes
  through full dynamic dispatch. The 64-way parallelism is real; the constant
  factor in front of it is ~100× worse.
- Python has **no `uint64`**. Bit 63 pushes values into arbitrary-precision
  bigint representation, which is a silent performance cliff and a correctness
  hazard for anything involving shifts or negation.
- A mailbox engine with piece-sets iterates only over the ~16 pieces that
  actually exist. A bitboard engine does work proportional to the *board*, not
  the pieces. In a language where per-operation cost dominates, fewer
  operations beats wider operations.

### 3.3 Even a perfect movegen wins little

Profiling says move generation is **37%** of search time. By Amdahl, an
*infinitely fast* movegen is a **1.6× ceiling** — roughly +0.5 ply, worth maybe
20–30 Elo. That is the entire upside, for a multi-week rewrite of the most
correctness-critical code in the project, in a direction the benchmark says is
slower.

### 3.4 What I think the underlying instinct is right about

"Stop worrying about speed" is the correct goal. The wrong mechanism is
bitboards; the right mechanisms, in order of payoff:

1. **Search fewer nodes, don't visit them faster.** Going from mediocre to good
   move ordering cuts the tree by 5–10×. That dwarfs any representation change,
   and it is where a 700 Elo gap actually lives.
2. **Incremental evaluation.** Update material + PST deltas inside make/unmake
   instead of rescanning the board at every leaf. This attacks the 22%
   evaluation slice at the root cause, and makes the eval cache unnecessary.
3. **Keep PyPy.** It is already a 2.4× multiplier and it is free.

### 3.5 The one thing bitboards genuinely buy

Bitboards make some *evaluation* terms nearly free — passed pawns, king-zone
attack counts, pawn structure — via precomputed masks and `popcount`. That is a
real benefit. But the same thing is achievable in mailbox with precomputed
per-square index tables, which v1 already does for rays (`_rays_from`).

**Recommendation: keep mailbox + piece-sets. Reject the bitboard rewrite.**
I'll build it if you still want it after reading this, but I'd be building
something the benchmark says is slower.

---

## Part 4 — What the field does that we didn't

From the research, the consensus practices we were missing:

| Practice | Status in v1 | Source |
| --- | --- | --- |
| Perft-verified movegen before anything else | ✅ done, kept | [CPW Perft](https://www.chessprogramming.org/Perft) |
| SPRT for every change | ❌ never used | [CPW SPRT](https://www.chessprogramming.org/Sequential_Probability_Ratio_Test) |
| One feature per test | ❌ bundled | [CPW Engine Testing](https://www.chessprogramming.org/Engine_Testing) |
| Known-strength calibration opponent | ❌ never built until today | — |
| Texel//eval parameter tuning | ❌ all hand-picked | [CPW Getting Started](https://www.chessprogramming.org/Getting_Started) |
| Test openings resembling real play | ⚠️ random 8-ply books | [CPW Engine Testing](https://www.chessprogramming.org/Engine_Testing) |
| Search improvements first, then eval | ❌ interleaved | [dogeystamp](https://www.dogeystamp.com/chess0/) |

The single most-repeated warning in the sources is the one that describes this
project exactly: *"Many new engine developers get stuck at the lower end of
rating lists due to no or improper testing."*

Rough published Elo values, for prioritisation (not gospel — implementation
dependent):

| Feature | Approx. Elo |
| --- | --- |
| Transposition table | ~150 |
| Killer moves + PVS | ~50 |
| TT in quiescence | ~25 |
| Singular extensions | ~36 |

---

## Part 5 — The v2 plan

### 5.0 Principles

1. **Nothing is merged without a passing SPRT.** No exceptions, no "obviously
   better" changes.
2. **One change in flight at a time.**
3. **A calibrated strength number exists before any tuning starts, and is
   re-measured at every milestone.**
4. **One move representation.** No dual pipeline.
5. **Correctness is proven by perft before speed is discussed.**

### 5.1 Module layout

```
engine2/
  board.py        board state, make/unmake, attack detection, Zobrist
  movegen.py      legal move generation (staged)
  eval.py         evaluation, incremental accumulator, tuning hooks
  search.py       negamax, quiescence, ordering, time management
  tt.py           transposition table
  uci.py          protocol adapter
  tune.py         Texel tuning driver
tools/
  calibrate.py    strength vs UCI_Elo-limited Stockfish   (exists, keep)
  sf_review.py    Stockfish game grading                  (exists, keep)
  sprt.py         fastchess wrapper + SPRT bookkeeping     (new)
  bench.py        node-count determinism check            (exists, keep)
```

Splitting `move_finder.py` (1,651 lines, 218-line `_negamax`) into
`search.py` / `eval.py` / `tt.py` is most of the "organised" the user asked
for, and it makes the SPRT discipline practical — a change confined to
`eval.py` is obviously not a search change.

### 5.2 `board.py`

Keep from v1 (it measured well and it is correct):

- `list[list[int]]` mailbox, integer piece codes, `PIECE_TYPE` lookup.
- `white_pieces` / `black_pieces` coordinate sets.
- Module-level deterministic Zobrist tables, incremental key updates.
- Precomputed ray tables (`_rays_from`) — 10.4% at identical node counts.

Changes:

```python
class Board:
    def make(self, move: int) -> int:       # returns packed undo token
    def unmake(self, move: int, undo: int) -> None
    def is_attacked(self, sq: int, by_white: bool) -> bool
    def in_check(self, white: bool) -> bool
    def zobrist(self) -> int
```

**One representation, and it is the fast one.** A move is a packed `int`
(from, to, flags) rather than a 5-tuple *or* an object — tuples allocate, ints
in PyPy's tagged range do not. The GUI gets a thin `Move` wrapper at the
boundary only, built by `Move.decode(packed, board)`; nothing inside `engine2/`
ever sees it. This deletes M6 outright.

**Incremental accumulator** lives here, updated inside `make`/`unmake`:

```python
self.material: int      # signed, White's perspective
self.pst_mg: int        # midgame piece-square sum
self.pst_eg: int        # endgame piece-square sum
self.phase: int         # tapering phase counter
```

`eval.py` then reads these instead of rescanning 64 squares. This is the
change that actually removes the 22%, and it makes the 1M-entry eval cache
(M8) deletable.

### 5.3 `movegen.py`

- `generate_legal(board) -> list[int]` — legal, not pseudo-legal, using the
  existing pin/check detection which is already correct and perft-verified.
- `generate_captures(board) -> list[int]` — for quiescence.
- **Staged generation** (later, behind its own SPRT): yield TT move → captures
  → killers → quiets, so a cutoff on the TT move never pays for generating
  quiets at all. This is a node-count win *and* a time win.

Gate: perft depths 1–6 from the start position, Kiwipete, and the standard
"position 3/4/5" suites, all matching published counts, before any search work.

### 5.4 `eval.py`

Order of construction, each behind its own SPRT:

1. Material + PST from the accumulator (tapered mg/eg). — baseline
2. Pawn structure: doubled, isolated, passed (with rank scaling).
3. **King safety** — the biggest v1 omission. Attacker count and attacker
   weight on the king zone, not just a pawn shield.
4. Mobility (v1 has this; re-measure it standalone, it was never isolated).
5. Rook on open/semi-open file, rook on 7th.
6. Threats / hanging pieces.

Then `tune.py`: **Texel tuning.** Fit every weight by minimising prediction
error against game outcomes over a large position set (our own Lichess games
plus a public set). This directly retires M7 — 34 guessed constants become 34
fitted ones — and it is the single highest-value item in the plan after depth.

### 5.5 `search.py`

Build order, one SPRT each:

| Stage | Feature | Note |
| --- | --- | --- |
| S1 | Negamax + alpha-beta + iterative deepening | baseline |
| S2 | Transposition table | ~150 Elo, biggest single item |
| S3 | Quiescence (captures + checks at first ply) | prevents horizon blunders |
| S4 | MVV-LVA + SEE capture ordering | |
| S5 | Killers + history | |
| S6 | PVS | ~50 Elo with killers |
| S7 | Null-move pruning | verify at low depth to avoid zugzwang loss |
| S8 | LMR | log-scaled; re-derive the table, don't inherit v1's |
| S9 | Aspiration windows | |
| S10 | Check extensions | |
| S11 | Futility / reverse futility | margins **fitted**, not guessed |

Everything from S6 down is re-earned, not ported. v1's versions were never
individually measured, so their value is unknown — porting them forward would
carry the same unmeasured debt into v2.

Time management is **stage S12, last**, and only after a calibrated rating
exists. Its thresholds are expressed as fractions of remaining moves, never as
absolute seconds (M5).

### 5.6 `tt.py`

- Fixed-size, power-of-two indexed, replace-by-depth-preferring-newer.
- v1 used an unbounded dict keyed by Zobrist. Bounded is both faster and
  memory-safe over a long bot session.
- Mate scores stored with ply-adjusted values rather than excluded outright
  (v1 excludes them, which loses real information).

### 5.7 Measurement protocol (the part that must not be skipped)

**Instrument selection, by change type:**

| Change type | Instrument |
| --- | --- |
| Provably score-neutral (speed) | `bench.py` node count — must be *identical* |
| Anything that can change a move | **SPRT via fastchess** |
| "Are we actually any good?" | `calibrate.py` vs `UCI_Elo` Stockfish |
| "Where did we go wrong in this game?" | `sf_review.py` |

**SPRT setup:**

```
fastchess -engine cmd=<new> -engine cmd=<baseline> \
  -each tc=10+0.1 -openings file=<book>.epd order=random -repeat \
  -sprt elo0=0 elo1=5 alpha=0.05 beta=0.05 -concurrency 4
```

Fixed rules: `-repeat` (colour-reversed pairs) always; never two matches
concurrently on this machine; the opening book resembles real play, not random
8-ply walks.

**Lichess deployment rules (fixing M1):**

- `challenge_mode: rated` — otherwise the rating never moves and matchmaking
  never corrects.
- Set `opponent_min_rating` / `opponent_max_rating` to a band around our
  *calibrated* rating, not around 3000.
- Lichess is for **validation and rating**, not for A/B testing. A/B testing is
  SPRT, locally, where we control the variables.

### 5.8 What carries over from v1 unchanged

Worth being explicit that this is not a from-zero rewrite:

- `pgn.py` — SAN resolution by matching against legal moves. Correct, tested.
- `uci.py` protocol layer — works, deployed, exercised by real games.
- `uci_client.py` — now doubles as the Stockfish driver.
- `sf_review.py`, `calibrate.py`, `bench.py`.
- Perft test suite and the random-walk make/unmake regression tests.
- The whole `gui/` package — it talks to the engine through a narrow contract
  and only needs `Move.decode` at the boundary.

### 5.9 Order of work

1. `board.py` + `movegen.py`, perft-clean to depth 6 on all standard suites.
2. `calibrate.py` run → **baseline rating with an error bar.**
3. S1–S3 (negamax, TT, quiescence), SPRT each.
4. Re-calibrate. Expect the largest jump here.
5. Eval stages 1–3, SPRT each.
6. `tune.py` Texel tuning pass.
7. Re-calibrate.
8. S4–S11, SPRT each.
9. Re-calibrate, then deploy **rated** with a correct rating band.
10. Time management last.

---

## Part 6 — Answering the original question

> "the complexity per perf of our engine is just not making sense anymore"

**The measurements say otherwise, and I'd argue against the rebuild.**

The engine is ~2133 Elo. It scored 1.0 points where its rating predicted 0.44.
A pure-Python engine at 2130 that outperforms expectation against 2900
opposition is not a project whose complexity failed to pay off — it is one
whose complexity paid off and was never credited, because the only scoreboard
we ever looked at was rigged by a config flag.

The real defect is not in the engine. It is in the **development loop**: 34
constants and ~11 search features, none individually validated, judged by
underpowered fixed-N matches against a sample that structurally could not
answer the question. That is why the complexity felt unaccountable — it *was*
unaccountable. But unaccountable is not the same as unearned, and the fix for
unaccountable is measurement, not deletion.

Throwing away a 2130 engine to rebuild it in a representation the benchmark
says is **slower** (§3) would cost weeks and, on the evidence, land somewhere
below where we already are.

### What I recommend instead

**Refactor, not rewrite.** Keep the search, keep the evaluation, keep mailbox.
Fix the four things that are actually wrong, in this order:

| # | Change | Why | Est. value |
| --- | --- | --- | --- |
| 1 | **Deploy `challenge_mode: rated`** with a rating band around 2130 | Closes the feedback loop that caused all of this. Costs one config edit. | the whole measurement program |
| 2 | **Stand up SPRT** (`fastchess`, elo0=0/elo1=5) | Every future change becomes decidable. Retires M3/M4. | prerequisite for everything |
| 3 | **Collapse the dual move pipeline** (M6) | 64 `for_ai` references, every invariant maintained twice. Pure complexity deletion, node-count-neutral so `bench.py` proves it safe. | 0 Elo, large maintenance win |
| 4 | **Texel-tune the 34 constants** (M7) | Highest-value *strength* item we have never attempted. | ~50–100 Elo |
| 5 | **King safety in eval** (§5.4) | Biggest single evaluation omission. | meaningful, SPRT will say |
| 6 | **Incremental eval accumulator** (§5.2) | Kills the 22% eval slice at the root; makes the 1M eval cache deletable. | speed → depth |

Items 3, 5 and 6 are exactly the "trim it, make it organised and efficient"
work that was asked for — they just land as targeted surgery on a working
engine rather than as a from-scratch rebuild.

**What stays true from Part 5:** the module split (§5.1), the measurement
protocol (§5.7), and the principles (§5.0) all still apply. They describe how
to work on this engine, and they are the part v1 genuinely lacked. Part 5's
build order applies only if we decide to rebuild anyway — it is kept as the
plan of record for that case, not as the recommendation.

### The one-line version (of Part 6)

We spent the project optimising an engine we had never measured, against
opponents it could not beat, using instruments that could not detect the
difference. The engine was fine. Fix the scoreboard first.


---

## Part 7 — Getting past 2130

Four new diagnostics, run after the calibration landed, say where the Elo is.

### 7.1 The engine throws away a third of its clock

Measured over six realistic positions, time actually spent vs budget given:

| Budget | Used (mean) | Utilisation |
| --- | --- | --- |
| 3.0s | 1.73s | **58%** |
| 5.0s | 3.44s | **69%** |

Two causes, both in `search_position`:

1. `SOFT_STOP_FRACTION = 0.45` refuses to *start* an iteration once 45% of the
   budget is gone. Iterations grow ~3-5x, so this fires constantly.
2. `except SearchTimeout: break` **discards the entire partial iteration.**
   Everything computed at depth N+1 before the clock ran out is thrown away.

Cause 2 is what makes cause 1 necessary — starting an iteration you can't
finish is only wasteful *because* the partial result is binned. The standard
fix is to keep a running best at the root: since root moves are re-ordered with
the previous best first, a partial iteration either confirms the best move or
finds something better, and both are useful. Then the soft-stop gate can move
much closer to 1.0.

Recovering this is roughly a 1.5x effective time multiplier, worth ~+30-50 Elo,
for a small diff in one function.

### 7.2 Errors cluster in the early middlegame

Stockfish depth-12, our moves only, all 43 games, bucketed by move number:

| Moves | ACPL | Blunders | Sample |
| --- | --- | --- | --- |
| 1-15 | **13.3** | 1 | 645 |
| 16-30 | **30.2** | 5 | 611 |
| 31-45 | 28.1 | 3 | 415 |
| 46+ | 24.1 | 1 | 292 |

The opening is clean — the polyglot book is doing its job. Play degrades
sharply the moment the book runs out and the position is still complex, and
half the blunders land in moves 16-30. That is the band where king safety and
depth decide games, and it is exactly where our evaluation is thinnest.

### 7.3 The engine is unobservable in production

`engine/uci.py` emits **no `info` lines at all** — no depth, no score, no nodes,
no pv. Lichess shows nothing, and there is no way to know what depth a real
game reached. Every diagnostic above had to be reconstructed offline. This is
0 Elo directly and a prerequisite for everything else.

### 7.4 Free endgame perfection is switched off

`<lichess-bot>/config.yml` has `online_egtb: enabled: false`. Lichess serves a
7-piece Syzygy tablebase over HTTP; enabling it gives *perfect* play in any
position with <=7 pieces. Our 46+ move ACPL is 24.1, so there is real money
here, and it costs one config line.

### 7.5 Roadmap, ordered by Elo per unit of effort

**Tier 0 — prerequisites (no Elo, unlocks everything)**

| Item | Effort |
| --- | --- |
| SPRT harness (`fastchess`, elo0=0 elo1=5) | half a day |
| `info depth/score/nodes/nps/pv` in `uci.py` | an hour |

**Tier 1 — cheap and high-confidence**

| # | Item | Est. Elo | Effort |
| --- | --- | --- | --- |
| 1 | Enable `online_egtb` | free, real | 1 config line |
| 2 | Keep partial iteration results + raise the soft-stop gate (§7.1) | +30-50 | small diff |

**Tier 2 — real strength work**

| # | Item | Est. Elo | Effort |
| --- | --- | --- | --- |
| 3 | Texel-tune the 34 constants + PSTs | +50-100 | 2-3 days |
| 4 | King safety (attacker count/weight on king zone) | meaningful, targets §7.2 | 1-2 days |
| 5 | Incremental eval accumulator (removes the 22% slice) | +0.3 ply | 1-2 days |

**Tier 3 — depth**

| # | Item | Est. Elo |
| --- | --- | --- |
| 6 | Staged movegen (no quiets when the TT move cuts off) | node-count win |
| 7 | Singular extensions | ~36 |
| 8 | Re-derive LMR/futility margins by tuning, not guessing | unknown, currently unmeasured |
| 9 | Capture history + history gravity in ordering | fewer nodes |

**Tier 4 — the wall**

NNUE is where meaningful further gains live, and it is also where pure Python
stops cooperating: inference needs fast vector math, which means numpy for the
accumulator and a real risk that per-move overhead eats the evaluation gain.
This is a project in itself, not a stage. Worth attempting only after Tiers 0-3
are exhausted and re-calibrated.

### 7.6 Realistic target

Tiers 0-3 plausibly land **2400-2600**. Beyond that, a pure-Python engine is
fighting arithmetic: ~1M nps under PyPy against Stockfish's 100M+, compensated
only by search quality. That is a good ceiling to aim at and an honest one to
state up front.

**Re-run `calibrate.py` after every tier.** The number is the point.

---

## Part 8 — First rated-game finding (2026-07-20)

Two rated games in, one win (rapid, vs 1909) and one loss (blitz 300+0, vs
2197). The loss is worth recording because of *how* it was lost.

Stockfish graded our play at **ACPL 14.0, accuracy 97.4%, zero blunders** — and
we still lost. The whole game turned on one move:

| Move | Played | cpl | Time spent | Eval before → after |
| --- | --- | --- | --- | --- |
| 29 | `f5f4` | **198** | 7.0s | −8 → +190 (White) |

Everything after move 30 is losing a lost position, not new error.

Position: `8/1p4p1/p1b4p/4ppk1/1PP5/1N3P2/P4KPP/8 b - - 2 29` — a
bishop-vs-knight endgame, six pawns each.

**It is not a time-management failure.** We spent 7.0s on it, generous for
300+0, and used 248s of 300s over the game (83% — the new clock code working;
the old one left 28–48% unspent).

**It is not a depth failure.** Our engine reaches depth 11 in 7s here.

**It is an evaluation failure, and a confident one.** We score `f5f4` at **+37
in our own favour** while Stockfish plays `e5e4` and calls our move ~200cp
worse. We then played `e5e4` ourselves on move 30 — the right idea, one move
too late.

Suspected gap: no bishop-quality terms at all — no bad-bishop penalty (pawns
fixed on the bishop's colour), no knight outposts, no bishop-vs-knight
imbalance. `KING_END_PST` and endgame passed-pawn scaling do exist, so the
basic endgame scaffolding is there.

**Deliberately not acted on yet.** This is n=1, and tuning evaluation from a
single game is exactly the "hand-picked test cases lie" failure this project
has already hit twice. The position is recorded here as a candidate test case;
the question is whether bishop-vs-knight endgames are a *pattern* across the
collected set. If they are, it moves ahead of Texel tuning in the queue.

---

## Part 9 — Piece-quality evaluation (queued, starts with research)

### Why: the audit

The evaluation understands **pawns and rooks**. It barely understands
**knights and bishops**, in either phase.

| Piece | Midgame terms | Endgame terms |
| --- | --- | --- |
| Pawn | doubled, isolated, passed | passed (scaled up) |
| Rook | open / semi-open file, 7th rank | same (not phase-aware) |
| King | PST + pawn shield | endgame PST |
| **Knight** | **mobility only** | **mobility only** |
| **Bishop** | **mobility + pair bonus** | **mobility + pair bonus** |
| Queen | mobility only | mobility only |

Missing entirely, in both phases: knight outposts, bad bishop (own pawns fixed
on the bishop's colour), bishop-vs-knight imbalance by pawn structure, threats
and hanging pieces, rook behind a passed pawn, king proximity to passed pawns.

This is not an endgame-specific gap. It looked like one because the first
rated loss happened to be a bishop-vs-knight endgame (Part 8), but the same
blindness applies from move 15.

### Order of work

**Stage 0 — research first, before writing any term.** Read how established
engines score piece quality: outpost definitions, bad-bishop formulations,
B-vs-N imbalance tables, threat evaluation, and endgame-specific scaling.
Chessprogramming wiki plus one or two open engines with readable evaluation.
The point is to *not* invent heuristics from intuition — this project has now
twice hand-labelled positions and got them backwards. Write down what the
field does and why before choosing.

**Stage 1 — decide what the collected games justify.** By then the 2-day
version-`daecc23` set exists, graded by Stockfish. Look for the *pattern*: are
B-vs-N endgames actually where we bleed, or was Part 8 a single bad game? Rank
the candidate terms by how often each would have changed a move we actually
lost by. This is what stops it being intuition-led.

**Stage 2 — implement one term at a time, each behind its own SPRT.** Never
two in flight. Candidate order, subject to what Stage 1 says:

1. Knight outposts (protected, on a square no enemy pawn can attack)
2. Bad bishop (penalty scaled by own pawns on the bishop's colour)
3. Threats / hanging pieces
4. Bishop-vs-knight imbalance keyed on pawn-structure openness
5. Rook behind passed pawn; king proximity to passed pawns (endgame)

**Stage 3 — re-calibrate** with `engine/tools/calibrate.py` and compare against the
~2133 baseline.

### The trap to avoid

Every term here is tempting to "obviously" get right by reasoning. The
project's own history says otherwise: `test_node_count` and the stability
heuristic were both built on confident intuitions that measured backwards.
Research first, then let the collected games rank the candidates, then SPRT
each one alone. A term that measures neutral gets reverted even if it is
"clearly correct" chess.
