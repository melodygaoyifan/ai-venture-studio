# ADR-049 — The refusal is measured, on its own axis

Status: accepted (v0.98.0)

## Context

ADR-046 gave this product the only refusal it has. A new request is read
against the ledger of what the product already promises, and one of three
things happens: it duplicates a promise and nothing is built, it contradicts
one and a person is asked (or, under `--yes`, the build proceeds and the clash
is written down as an unapproved SCR), or it is a real addition and is built.

Nothing measured it. The product bench asks one question — *can it build the
thing that was asked for* — over four real cases, and every one of those cases
asks for a product to exist. A gate that never fires scores exactly the same as
a gate that fires correctly, because in both readings the run completes and the
tasks build.

ADR-048 then found out what that costs. The gate shipped in v0.96.0 **inert in
Chinese** — `requirements.tokens()` required an ASCII letter, so every score was
zero, retrieval returned an empty slice, and the reconciler reported "no
existing requirement matched" for every request in the language every benchmark
case is written in. It never fired and it never errored. Run 17 would have
measured it as a working gate, and the bench row would have said so.

That is the failure mode this record is about: **the one thing this bench
cannot see is a refusal that does not happen.**

## Decision

### A case says which axis it is on, and the axes do not mix

`ProductCase` gains `axis: "build" | "increment"`. The four cases in
`benchmarks/products-real` that have always produced the headline series stay
on `build` and are the *only* contributors to build rate, probe pass rate and
clean review rate. Increment cases feed one new rate — the **gate rate** — with
its own denominator, its own `unmeasured` list, and its own block in the saved
result.

They are separate rather than combined for the reason ADR-035 gives about
denominators, applied to a new kind of case: **"did it build what was asked"
and "did it correctly decline to build" are different questions.** An increment
case whose correct outcome is `already_satisfied` builds nothing on purpose.
Averaged into the build rate that is a `0.0` — the same score a case earns for
failing outright — and the series would step down for the system doing exactly
the right thing.

The split also keeps runs 13–17 readable as one series. `cases_total` in the
saved result is now the **build-axis** count, so "of 4" keeps meaning in run 18
what it meant in run 13, and `bench_criterion`'s floors keep measuring the same
population they were set against.

### A case declares its expectation before the run

`feature_expectations` pairs by position with `feature_fdrs`, one of
`already_satisfied` / `raises_scr` / `completed`. A mismatch in length, or an
unknown value, is refused at **load** — before hours of wall-clock — because a
case file that means something other than what it says makes every row after it
unreadable.

An expectation written after the run is not a measurement. This is the same
rule the probes already follow.

### A raised SCR outranks the status, in both directions

Under `--yes` a contradiction **proceeds to build** — ADR-046 is explicit that
`--yes` may approve work but may never approve a change to what the product
promises — so its status reads `completed`, character for character identical
to a gate that never fired. The unapproved SCR on disk is the only thing that
distinguishes them, so the harness reads `.mas/scr/SCR-*.yaml` for a newly
written `status: proposed` entry rather than trusting the status.

It outranks in the other direction too: a follow-up expected to be a clean
addition that instead raised a contradiction scores `raises_scr` and is
**wrong**, not a passing `completed` row. A gate that fires too often is a
different failure from a gate that never fires, and both have to be visible.

### The real case is Chinese, and asks all three questions

`benchmarks/products-real/05-increment-repairs.yaml` builds a small 报修
backend whose FDR promises, on purpose and with a reason given, that a
submitted repair cannot be deleted. Then three follow-ups in the founder's own
later vocabulary: the same promise reworded (duplicate), deleting one's own
repair (contradiction), and rating a completed repair (clean addition).

Chinese because that is the language the gate was inert in while every English
test passed, and a case quietly rewritten into English would restore the blind
spot. All three expectations because a gate that only ever says no scores 100%
on a case that only ever asks it to say no; a test pins that the case contains
one of each.

## What this reverses

Nothing about how a product is built or reviewed. It reverses one thing about
the scoreboard: **`cases_total` is no longer "every case file in the
directory."** It is the build-axis count. That is a narrowing, and it is the
narrowing that makes the change safe — the alternative was a fifth case
silently redefining every rate in the series it joined, with the percentages
giving the reader no way to notice.

## What stays out

- **No floor, no kill criterion, no gate on the gate rate.** It has never been
  measured; a threshold set against zero observations is a number invented to
  look rigorous. Run 18 is the first reading. ADR-034's rule holds: a series
  earns its floors.
- **The 8-hour bench timeout is unchanged.** Run 16 spent ~2.97h over four
  cases; a fifth case that builds a base product and then runs three follow-up
  builds should add roughly 2h, leaving the margin at about 1.6× rather than
  2.7×. That is smaller and it is still a margin. Raising the ceiling on an
  estimate would be a change made against no observation; if run 18 comes close
  to it, the run itself will say so.
- **The synthetic case `06-already-promised` is not retired.** It is fast,
  hermetic-adjacent and checks the same path against fixed probes; the real
  case measures the same gate against a product the system built itself, which
  is a different and slower thing.

## Mechanism

- `ProductCase.axis` / `.feature_expectations`, validated in
  `model_post_init` — paired by position, refused at load.
- `IncrementResult` (index, fdr, expected, actual, correct, detail) and
  `CaseResult.gate_rate`, which is `None` when the case asked nothing rather
  than `0.0`.
- `_proposed_scrs()` reads SCRs by name and `status: proposed`, so an SCR a
  human later approved stops matching; `_score_increment()` lets a raised SCR
  outrank the status either way.
- `BenchSummary.gate_rate` / `.gate_unmeasured` / `.cases_total` /
  `.gate_cases_total`; `save_summary` writes a `rates.increment` block.
- `avs product-bench`, the Discord bench alert and the cadence one-line read
  each report the gate on its **own** line with its own denominator — never
  folded into the three rates, because a reader shown one number cannot tell
  which question it answered.
- Tests: `tests/test_product_bench.py` pins the axis split, the `None`-not-zero
  rule, the SCR-outranks-status rule in both directions, load-time refusal of
  unpaired expectations, that the real increment case asks all three questions
  in Chinese, and — the comparability guard — that the build series is still
  exactly four cases.
