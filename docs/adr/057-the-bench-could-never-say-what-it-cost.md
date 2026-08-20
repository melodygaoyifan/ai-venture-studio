# ADR-057 — the bench could never say what it cost

**Status:** accepted (2026-08-20)

**Answers:** "what will run 18 cost me" — a question the product benchmark has
been unable to answer for its entire life, while writing the answer to disk
once per case and then deleting it.

**Reverses:** nothing. It fills a hole in ADR-032's transparency story: the
spend module measures and says, and the one path whose cost people actually
ask about was never wired to it.

## Context

Every product-bench result file records `duration_s`. Run 17's row says a case
took 3438 seconds and then died. What it does not say — what no result file
in the series says — is what those 3438 seconds bought.

That number decides things. Whether to rerun. Whether to add a sixth case.
Whether a nightly cadence is affordable. Until now the only way to get it was
to open the provider console, find the day, and guess which charges belonged
to which run.

The data was never missing. Three facts, all long-standing:

1. `spend.record()` meters **every** provider call — model, input tokens,
   output tokens, stage, stop reason.
2. `autopilot` flushes that buffer into the workspace root between tasks, so
   the rows are already attributed to the case by construction. No per-call
   tagging is needed and none was ever missing.
3. `product_bench.run_case` builds each case in its own `mkdtemp` and deletes
   that tree in a `finally`.

The ledger was complete, correct, and inside the directory being deleted.

This is ADR-051's shape again — *one control, two call paths, the second
silently does less*. Cost metering is read back by `build`, `autopilot`,
`graph`, `studio`, `gepa`, `smoke` and `cli`. It was not read back by
`product_bench`, the one path that spends the most and the one whose cost a
human is deciding on.

## Decision

Read the ledger on the way out of the case, before the tree goes.

- `CaseSpend` on every `CaseResult`, and a summed `BenchSummary.spend`.
- Populated on the success path, and on the crash path via
  `exc.avs_case_spend` — the same channel the preserved-workspace path already
  travels on, for the same reason: the caller builds the error row and this is
  the only way the number reaches it. A crashed case is where "what did that
  cost" matters *most*; run 17 spent 3438 seconds and died, and the money was
  as unrecoverable as the measurement.
- One `flush()` first, because `autopilot` flushes *between* tasks and the
  last task's rows are still buffered when the case returns.
- Rendered on the CLI line under the rates, and in the Discord alert — whose
  docstring already argued that this run "costs hours of wall clock and real
  money on the founder's own key", and then reported only the hours.
- Written to the result file as a `cost` block **outside** `rates`, because it
  is not a rate and `bench_criterion` reads that block.

### Unpriced is not zero

`CaseSpend.usd` is `float | None`, and it is `None` — never `0.0` — when no
price covered the models the case used. This is ADR-053's rule applied to
money. A run nobody could price and a run that cost nothing are different
facts, and the difference is exactly the one the reader is trying to
establish. `unpriced_calls` rides along, so a partially-priced total announces
itself as a **floor** rather than passing as a total.

### Prices come from the operator's repo, not the case's workspace

Two different things live in two different places, and folding them into one
is the mistake this decision nearly shipped. The token **ledger** is written
inside each case's throwaway workspace. The **price table** is
`.mas/cost-model.yaml` in the repo the bench was invoked from, put there by
`avs prices --import`.

A directory `mkdtemp` created seconds ago has never held an operator's prices
and never will. Pricing a case against its own `.mas/` would report every call
unpriced, forever — technically honest under the rule above, and useless for
the only question the number is recorded to answer. The cost model is loaded
once from `repo_dir` and handed down; loading it per case would additionally
let a price table edited mid-run split one result file across two of them.

### A resumed row contributes nothing

Its cost was paid by the run that measured it and belongs to that run's total.
A `--resume` run that re-counted banked rows would inflate the series, and the
inflation would grow with how many times a flaky run was resumed — which is
precisely when someone is looking at the cost.

### The total counts cases the rates exclude

Deliberately opposite to ADR-035. A case that crashed or was refused still
spent money, and a total that dropped it would answer "what will this cost me
next time" with a number that has never been true. Rates are about capability
and must exclude the unmeasured; cost is about the bill and must not.

## What stays out

- **No cap, no refusal.** ADR-032 stands: the spend module measures and says,
  and nothing in it refuses. Budget enforcement belongs at the provider
  account that does the billing. Nothing here gates anything.
- **No prices in code.** Prices rot. The table stays operator-owned in
  `.mas/cost-model.yaml`, seeded from `prices.yaml` only on explicit
  `avs prices --import`, and a price already there is never overwritten.
- **No estimate when there is no price.** The gap is reported, not filled.

## What keeps this honest

- Metering never raises. `_case_spend` swallows its own failures and returns
  `None`: cost is a reading *about* the run and must not be able to change it.
  A bench that failed because it could not price itself would be a new way to
  lose measurement, which is the failure mode this whole area exists to stop.
- `tests/test_bench_says_what_it_cost.py` pins the four distinctions that make
  the answer honest rather than merely present: unpriced ≠ zero, partial ⇒
  floor, resumed ⇒ zero contribution, prices come from the operator's repo.
- The resumed-row test drives `run_product_bench` rather than re-summing the
  rows itself. A test that restates the production arithmetic passes whether
  or not the production arithmetic is wired in — which is the class of test
  that let the gap exist this long.
- The caveat travels **in the file**, not only on the terminal. The person who
  opens a result in six months is exactly the person who cannot go back and
  ask whether the price table was complete.

## The lesson worth keeping

The bench recorded, from its first run, the one dimension that was free to
record. Wall clock comes from `time.monotonic()`; cost required calling a
module that already existed, in a place that was already the right place.
Nothing was hard and nothing was missing. The measurement was simply never
asked for, and its absence looked like nothing at all — an empty field is
invisible in a way a wrong field is not.

When a system reports one dimension of a cost, check whether that is the
dimension that matters or merely the one that was easy.
