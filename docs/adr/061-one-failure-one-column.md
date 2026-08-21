# ADR-061 — one failure, one column: what run 18 had already recorded

**Status:** accepted (2026-08-21)

**Answers:** the second half of the founder's question behind ADR-060 — *can't
we just find the existing issues?* ADR-060 swept the source. This one mines the
**result files already on disk**, at the per-outcome level `HISTORY.md`'s
paragraphs never reached.

**Reverses:** half of the run-16 correction in `benchmarks/results/HISTORY.md`.
That reversal is argued below rather than performed quietly.

## Context

Run 18 cost $67.88 and five hours and seventeen minutes. Its rows were read for
the findings that became ADR-058 and ADR-059. Two more were sitting in the same
file, and both had gone unnoticed for the same reason: each moved a headline
number in the direction of **worse**, which is the direction nobody audits.

### The double-counted zero

`03-groupbuy-auto` was blocked at planning on the lane collision (ADR-059),
built 0 of 0 tasks, and recorded:

```
tasks 0/0 · failure_reason "planning blocked: lane collision: t1 (api) and
t3 (orders) both expect 'app/models*.py'"
probes: [probe-generation · passed=false ·
         "probegen produced no probes after a retry — case behavior
          UNMEASURED, scored as a failure"]
```

`build 0.0` is correct: ADR-035 is explicit that a case which ran and built
nothing scores a real zero. `probes 0.0` is the same failure, charged again.
There was no product to probe. The run's probe rate over its four build-axis
cases was reported as **75%** — `(1.0 + 1.0 + 0.0 + 1.0) / 4` — when three
cases had been probed and all three passed.

The correct rule was already written in this file, verbatim, on
`clean_review_rate`, one property below the one that got it wrong:

> Deliberately NOT the zero that `build_rate` now returns: a case that built
> nothing has no review to be clean, and the failure is already fully counted
> one column left.

### Our broken instrument, charged to the product

`05-increment-repairs`'s `the-real-addition-still-landed` failed on
`SyntaxError: unexpected character after line continuation character` — a
Python backslash continuation inside a YAML folded scalar, which could never
have parsed in any run. That specific pair of probes was fixed when run 18 was
recorded, and `tests/test_every_probe_compiles.py` closed the class for probes
written into a case file.

It cannot reach the probes `probegen` **writes during the run**, which is the
larger population: case 03 declares none of its own.

## Decision

### A case that built nothing has no probe reading

`probe_pass_rate` returns `None` when `tasks_built == 0`. The build zero stays,
at full weight, where the failure happened.

### A probe that cannot parse is ours

`ProbeResult.harness_fault` is set when the probe itself is broken.
`run_probe` compiles every probe — generated ones included — before running it,
and returns `harness_fault=True` with the reason. The synthetic
`probe-generation` entry a nothing-built case produces is flagged the same way,
and its detail now says which of the two facts it is stating.

Harness faults stay **in the row** and leave the **denominator**. Excluded from
the record is how a defect in our own instrument stops getting fixed; excluded
from the rate is how it stops being charged to the software it was measuring. A
case whose every probe was ours has no reading — `None`, never 100%. Excluding
a broken instrument must not manufacture a pass.

### The exclusion has to be visible, per case

`BenchSummary.no_probe_reading` names the build-axis cases that were measured
and still contribute nothing to the probe rate. `unmeasured` cannot carry them:
an unbuilt case *is* measured, and its build zero is real. The CLI prints the
narrower denominator under the rates; `notify.bench_alert` carries the same
sentence, because a qualifier that reaches only the operator's screen is one
the 3am reader did not get.

### Where this reverses run 16

The run-16 correction says of case 02 — also blocked at planning, also unbuilt
— *"its two probes failing were correct: there was no product, and that is a
fact about the run."* Both readings cannot be true.

This one wins because the three rates exist to say **where** the pipeline
failed. A probe column that mirrors the build column is not an independent
measurement of anything; it is the build failure printed twice, in a scoreboard
whose one job is to answer "did this get better". Under the current rule run
16's honest reading is `build 75% · probes 100% over 3 of 4 · clean 31%`.

What run 16 got right is kept, and is the more important half: **the exclusion
must be per-case and it must be printed.** A rate silently averaged over fewer
cases than the run is the defect, not the exclusion. That note is what
`no_probe_reading` exists to satisfy.

### `summarise` is now a function

Every rule about what counts toward which rate lived in the tail of
`_run_product_bench`, unreachable without executing a whole bench run. Both
defects in this record were found by reading result files rather than by a
test, because there was no way to write one. It is a module-level function now.

## What stays out

- **No re-scoring.** Run 18's file says `probe_pass_rate: 0.75` and keeps
  saying it; run 16's numbers stand as recorded. They were computed under the
  rule in force, the files name the version that produced them, and re-scoring
  history to match new code is how a series stops meaning anything (ADR-051).
  `HISTORY.md` carries both corrected readings beside the originals, and a test
  pins run 18's recorded 0.75 against exactly this temptation.
- **No forgiveness.** A case that builds nothing still scores `build 0.0`. This
  record removes a duplicate, not a penalty.
- **No probe rate manufactured from an empty denominator.** Every path that
  runs out of probes returns `None`.

## The trap this walked into and out of

Once a nothing-built case has no probe reading, a run where *every* case failed
writes a null probe rate — and `bench_criterion` skipped any run with one. The
worst reading the series can produce would have become the one the kill
criterion could not see, and the change would have shipped looking like an
improvement in honesty.

`BenchRun.probe_pass_rate` is optional now, and `below_floor` judges the floor
it has: a missing probe rate is not a passing one and not a failing one. A run
with no `build_rate` at all is still skipped — that one made no claim.

`test_a_run_that_built_nothing_is_still_judged` exists because the full suite
caught this, not because I foresaw it.

## The lesson worth keeping

ADR-060's sweep asks whether a fact reached anyone. This asks the cheaper
question first: **what is already recorded that nobody has read?**

Two findings, one afternoon, no API spend, out of a run that had already been
read twice for other purposes. Before paying for the next measurement, it is
worth checking what the last one is still holding — especially the numbers that
came out worse than expected, because a number that flatters gets audited and a
number that disappoints gets accepted.
