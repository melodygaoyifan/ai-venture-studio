# ADR-063 — the bench is not on a timer: change, not age, buys the $67.88 run

**Status:** accepted (2026-08-21)

**Answers:** the fourth strand of the founder's question — *why do we need
endless batch running?* The honest answer was that nothing said when to stop.

**Reverses:** ADR-036's weekly bench cadence, in the trigger only. The
watchdog, the daily due-check, and every other loop's weekly rule stand.

## Context

`avs cadence` watches three recurring loops and fires the due ones from a daily
LaunchAgent. Two of them cost nothing to run. The third is the product-bench:
four labelled real products driven end to end through the full autopilot,
**$67.88 and about five hours of API time per run**, and it was scheduled the
same way as the free ones — seven days since the last dated result file.

Run 19 was about to be bought on exactly that basis. Nothing in the framework
had necessarily changed since run 18; the calendar had.

The mismatch is between what the number measures and what the trigger watches.
This series measures the **framework's** capability. The framework changes when
a release changes it. Days are a proxy for that, and it is a proxy that breaks
in both directions at once: it buys runs over an unchanged system, and — as
ADR-057 already noted in `_bench_rates` — it reads "ok, 4d" while the newest
numbers came from nine releases ago.

## Decision

**The trigger is the reading's own `avs_version`, with a floor and a ceiling.**

```
due  ⟺  (measured_on ≠ running version  AND  age ≥ BENCH_MIN_SPACING_DAYS)
     OR  age ≥ BENCH_DRIFT_BACKSTOP_DAYS
```

`BENCH_MIN_SPACING_DAYS = 7`, `BENCH_DRIFT_BACKSTOP_DAYS = 90`.

- **The floor** because change is necessary and not sufficient: ten releases in
  a week are not ten benchmark runs' worth of news.
- **The ceiling** because the model underneath this system changes whether or
  not we ship, and a criterion that only ever asks after *our* edits cannot see
  provider drift.

A result file recording no `avs_version` (runs before 15) counts as **changed**.
An unknown build is not evidence of the same build, and the direction to fail
in is the one that measures rather than the one that skips forever.

**The change is strictly cheaper and never more expensive.** Every date on
which the new rule fires is a date on which the old 7-day timer would also have
fired — the new rule only ever declines runs the old one would have bought. A
test walks 120 days of a fixture and asserts exactly that, rather than leaving
the claim as prose.

### The ceiling is the load-bearing half

The obvious version of this change is "run it when the version changes", and
that version is the failure this module names in its own source:

> a scheduler watching an empty set reports "all clear" forever, which is the
> one thing a watchdog must never do

"The version has not changed" is precisely the sentence that talks a watchdog
into that state. A quiet release month and a bench that has been switched off
produce identical rows, indefinitely, and the row says `ok` through both. So
the backstop is not a safety margin bolted on afterwards; it is the reason this
is a change of cadence rather than the removal of one. A test asserts
`BENCH_DRIFT_BACKSTOP_DAYS <= 120`, because the way this becomes "never" is not
a code change — it is someone setting it to 3650 "for now".

This is ADR-059's shape (*the check that cannot fire*) caught before it shipped
rather than after.

### The reason travels with the verdict

`DUE (9d)` reads as a timer, and a timer is no longer what raises this. A
reader deciding whether to spend $67.88 needs to know which of the two rules
fired, so `LoopStatus` gained `due_because` and it appears in the scheduler
table, in `run_due`'s "not due" detail, and in the alert — beside the command,
which is the surface where the money actually gets spent:

```
**bench** is overdue.
  the framework changed since the last reading — measured on v0.109.0,
  running v0.110.0
  `avs product-bench --cases-dir benchmarks/products-real`
```

Empty on an `ok` row: a sentence explaining why something is due, printed
beside a thing that is not due, trains the reader to skip the column.

## What stays out

- **The other two loops keep their weekly timer.** Compound and sweep cost
  nothing, and a cheap loop's cadence is not a decision worth this machinery.
- **Nothing here decides whether the numbers are good.** Rule 1 of this module
  is unchanged: it states, it does not decide. Whether the rates fire the kill
  criterion remains `bench_criterion.evaluate`'s call.
- **The version comparison is exact string equality.** Not "minor version
  changed", not a diff of `src/` — a release is the unit this project ships and
  the unit `avs_version` records, and any cleverer rule would need its own
  argument about which changes can move a capability number.

## Mechanism

`tests/test_the_bench_is_not_on_a_timer.py`, ten tests, weighted deliberately:
three pin the run that no longer gets bought, four exist solely to keep this
from becoming a check that never fires, and three pin that the reason reaches a
person. Run 19's exact fixture — eight days old, same build — is a test.

An aftershock of ADR-061 was found in the same module on the way, by going
looking rather than by hitting it: `_bench_rates` required both rates to be
present, and since ADR-061 a run where every case failed to build writes
`probe_pass_rate: null`. The scheduler line would have gone **blank** for the
single worst run the series can produce. It now reports the build rate it has
and says `probes not measured (nothing built to probe)`.
