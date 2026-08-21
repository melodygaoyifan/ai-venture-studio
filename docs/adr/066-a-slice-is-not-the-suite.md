# ADR-066 — A slice is not the suite

**Status**: accepted · **Date**: 2026-08-21 · **Release**: v0.111.0

## Context

Bench run 19 is owed. A run is five cases and roughly five hours against the
real provider, and the account behind it cannot currently afford that in one
sitting. So: is there another way to buy it?

The obvious one is already supported. `avs product-bench --limit N` runs the
first N cases; ADR-052 banks every measured case as a checkpoint; `--resume`
reuses a banked row when the case file, the build, and the provider all match.
Buy a case at a time as credit allows, then close with one un-limited
`--resume` run that measures whatever is left and reports the whole suite.

That plan is sound. What was not sound was what each slice did to the ledger on
its way past.

```python
cases = load_cases(cases_dir)[: limit or None]
```

`cases_total` counts the cases the run was **handed**, not the cases the suite
**has**. So `--limit 1` over a five-case suite wrote a scoreboard reading
`1 of 1`, and `save_summary` dual-wrote it into the tracked
`benchmarks/results/` where the capability kill criterion reads.

Every guard on that ledger passed it through. It is not partial —
`cases_measured == cases_total`. It is not aborted; the environment was fine.
It is not simulated; the cases it ran were measured for real, at full price,
against the real provider. It was, as far as any reader could tell, a complete
reading of the suite.

**And the suite contains a case that has built nothing in two consecutive
runs.** Run 18's case 03 hit a lane collision it was never shown a remedy for
and built nothing; the increment gate scored 0% in the same run. A slice
landing on either reads `build 0% over 1 of 1` — below floor, not partial, not
excluded — and `CONSECUTIVE_RUNS_TO_FIRE = 2`. Two purchases of a bench nobody
could afford to run would fire a criterion whose only remedy is a human
deciding whether to kill the project.

This is the third instance of one shape, and it is worth naming as a class:

| ADR | The cheap thing | What it corrupted |
|---|---|---|
| 053 | a one-case increment run | `_avg([])` → `0.0` entered the ledger below floor |
| 056 | `--provider mock` | a regex table's output read as a capability measurement |
| 066 | `--limit N` | a slice of the suite read as a reading of the suite |

*The cheap substitute for an expensive measurement can corrupt the ledger the
measurement lives in.* Check that before running it — which is exactly what
ADR-056 said, and the check has now paid for itself twice.

## Decision

**The denominator travels.** `--limit` bounds what the run *pays for*, never
what the scoreboard *counts*. The full suite is loaded, and every case beyond
the limit gets a row with `autopilot_status = "error: not run: --limit N"`.
That is deliberately the same shape as the abort rows: the leading `error`
keeps the case out of every rate (ADR-035, never a 0.0 for a case nobody
asked), and lands it in `unmeasured`, so the file reads `1 of 5` and names the
four it never asked.

The rest of the status is the part `unmeasured` cannot carry. A bare list of
names cannot distinguish a case that crashed from a case nobody paid for, and
those have different next steps (ADR-058's finding, applied before it could
recur).

**A truncated run is not a reading of the suite, and the tracked directory is a
series of readings of the suite.** So it stays in `.mas/` — a slice is a real
purchase, its scoreboard is how the operator checks what they bought, and its
checkpoints are what the closing run spends — and it does not reach
`benchmarks/results/`. `bench_criterion._scan` refuses one that arrives anyway
(hand-copied, or written by a build older than this rule) and **names it**,
with the next step attached: the cases it measured are banked, so `--resume`
closes the suite without re-paying for them.

Two layers, because either alone is silent about the other's case — the same
argument, and the same pair of layers, as ADR-056.

**`truncated` is read off the rows, not off the flag.** `--limit 6` over six
cases asked every one of them and is a complete reading. Refusing it for
carrying a flag would be a second defect wearing the first one's clothes — and
that defect was live in this change's first draft, because `limited_to` is a
model field and `model_dump` put it in the payload unasked. The key is placed
by `save_summary` or not at all, the way `cost` is.

**Every reader of these numbers, in one change.** ADR-051's rule: the ledger
(`bench_criterion`), the watchdog (`cadence._bench_status`, which would
otherwise report "ran recently, all clear" about a suite nobody read), and the
alert (`notify.bench_alert`, whose heading now says `SLICE` for the same reason
it says `SIMULATED` — real percentages, real provider, and nothing in the
numbers to mark them). The watchdog asks the criterion rather than re-deriving
the exclusion, so the two cannot drift.

## Consequences

- **Buying run 19 in slices is now safe, and is the recommended way to run an
  expensive bench on a constrained account.** Slice as far as credit allows;
  close with one un-limited `--resume` run, which is the reading. All the
  pieces must share one build and land within the 14-day checkpoint read
  window (ADR-052), so `src/` must not change mid-purchase — a release
  invalidates every banked row.
- A run that both aborted and was limited carries both keys and is classified
  as aborted, which is the more actionable of the two. Both exclude it.
- **`--limit` was load-bearing in the test suite as a shorthand for "the suite
  is small", and that shorthand is now false.** Twelve tests across
  `test_product_bench.py` and `test_unmeasured_is_one_decision.py` passed
  `limit=2` to get a two-case denominator; they now copy two real case files
  into a tmp directory, so the suite genuinely is two cases and the assertion
  is about the thing it names. In `test_bench_resume.py`, where the tests were
  about aborts and were reading a number the limit happened to control, they
  count by reason instead of by list length.

  Worth recording *how* that was found: the targeted runs over the files this
  change edited were all green, and the twelve failures appeared only in the
  full suite. A blast radius measured by "which files did I touch" misses every
  caller that depended on the old meaning — which is the entire population that
  a semantic change breaks.

## Mechanism

`tests/test_a_slice_is_not_the_suite.py`, 16 tests, in four groups: the
denominator, the tracked-directory guard, the criterion's refusal, and the
alert. Three deserve naming:

- **`test_two_slices_cannot_fire_the_kill_criterion`** — the scenario end to
  end, and the reason this is not a tidiness fix.
- **`test_a_limit_that_covered_the_suite_is_still_a_reading`** — the inverted
  defect, which this change shipped in draft and a control run caught.
- **`test_a_complete_run_still_reaches_the_tracked_results`** — the half that
  proves the guard is narrow. A guard that kept everything out would pass every
  other test here and silently end the series.

**The defect was verified against the deployed build before it was fixed**, not
inferred from the diff: `/Users/yifangao/miniconda3/bin/python` running 0.110.0
reports `--limit 1` over a six-case suite as `1 of 1`, with `unmeasured: []`,
and writes it into the tracked ledger. That control also caught the
`model_dump` bug above, which is the ADR-054 lesson holding: running the thing
beats reading it.

## References

- ADR-052 — checkpoints and `--resume`; what makes slicing possible at all
- ADR-053, ADR-056 — the other two instances of this shape
- ADR-035 — a case that was never asked is dropped from the rate, never a 0.0
- ADR-051 — every reader of a fact, closed in one change
