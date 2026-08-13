# ADR-035 — an unmeasured case is not a zero, and it is not silent

**Status:** accepted (v0.83.0, 2026-08-13)

**Reverses:** the averaging in `_run_product_bench` that entered a crashed
case as `0.0` in all three rates, and the `{}` the probe frame returned in
place of a 4xx body. Also reverses the reasoning behind part of `2bb4808`
— see *What this corrects*.

**Does not reverse:** the floors (build 60% / probe 50%), the kill
criterion, ADR-034's watched bench loop, or the rule that a bad result is
a valid measurement rather than a broken run.

## Context

Run 12 (2026-08-13) was the first bench run a scheduler ever performed.
It reported **build 75% · probes 65% · clean 48%** and exited 0, so
`avs cadence --notify` printed *no alert: nothing needs a person*.

Three of those statements were misleading, for three separate reasons.

**Case 04 never ran.** Its own `pytest -q` did not return within 300s.
`run_test_gate` catches `TimeoutExpired` on its own path, but four other
callers — build, autopilot, correction, fixpr — reach the runners
directly and had no guard, so the exception raised out of everything
above it and killed the case an hour into the run.

**The dead case then scored zero.** `_avg` averaged `0.0` for a case with
no denominator, which is how "we did not measure this" got recorded as
"the machine failed at this". It cost 22 points of probe rate. The launch
PRD's only kill criterion reads exactly that number against a 50% floor,
so a hung subprocess was two more bad weeks away from firing a
**capability** verdict about the writer.

**And nobody was told.** `product-bench` exits 0 whether or not a case
died, because a case erroring is data rather than a run failure. The
cadence loop therefore reported `ok` with rates attached — the same
absence-as-clean-pass shape ADR-033 and ADR-034 each removed one level
further out, now one level further in.

## What this corrects

Run 12's case 03 failed two probes with `AssertionError: no error field:
{}`. The product was correct: booted by hand it answers `400
{"error": "id must be a base-10 integer: 'abc'"}`. The frame in
`probegen.BOOT_FRAME` was discarding it — `urllib` raises `HTTPError` on
every 4xx and puts the body on the exception, and `call()` returned
`e.code, {}` without ever calling `e.read()`.

Run 7 produced the identical `{}` failures in the same case. The response
then (`2bb4808`, and the `web.yaml` rule beside it) was to require
products to put a human-readable `error` field in 4xx bodies. That rule
is good and stays. But it was written to fix a symptom in the product
that was actually a defect in the measurement, and it could never have
worked, because the probe could not see the field no matter what the
product wrote. **A harness that cannot read the answer will keep
reporting that the answer is wrong, and each round of that produces a
plausible fix one layer too low.**

One more thing this exposed. The metric definition at
`metrics/product_bench_capability.md` has excluded *"cases that died on
harness noise rather than on the product under test"* since 2026-07-27,
naming run 4 and run 5 as the precedent. The code never did it. **A stated
exclusion that nothing enforces is a comment**, and this one read as a
guarantee for two and a half weeks.

## Decision

1. **A rate averages only over cases that produced its denominator.** No
   build rate without tasks, no probe rate without probes, no clean-review
   rate without built tasks. A case that ran and built nothing still
   scores a real `0.0` — that is a failure, not an absence.
2. **The denominator travels with the number.** `cases_measured`,
   `cases_total` and `unmeasured` are written into the saved result, so a
   later reader of the series can tell 75%-of-four from 75%-of-three. The
   cadence line and the Discord alert carry it too — and so does
   `bench_criterion`, the module that actually fires. Gate PL5 is a human
   deciding whether to kill the project on two numbers; the sentence they
   read now ends `(over 3 of 4 cases)` when that is what happened, rather
   than making them open the YAML to find out. Files predating this ADR
   carry no denominator and are read as complete, which is what they were.
3. **A run that could not measure a case exits 3.** Not because the
   result is bad — a bad result is the measurement working — but because
   the harness broke and nothing else will say so. Since v0.81.0 a
   non-zero exit from a scheduled loop reaches Discord.
4. **A hanging suite blocks its gate instead of killing the run.**
   `_run_and_classify` converts `TimeoutExpired` into
   `TestReport(status="error")`, which already blocks APPROVE. An
   unprovable suite must not pass, and must not take the run down.
5. **The probe frame reads the error body.** `call()` decodes `e.read()`
   on `HTTPError` exactly as it decodes a success body.

## What keeps this honest

- `test_a_real_zero_is_still_a_zero` — the exclusion is for absent
  denominators only; a case that ran and built nothing keeps dragging the
  build rate down. Without it, decision 1 would be a way to hide failures.
- `test_a_bench_that_measured_everything_stays_quiet` — poor rates are
  not an alert. If they were, the channel would cry wolf about the
  benchmark doing its job, which is what ADR-033 removed.
- `test_a_probe_can_read_the_error_body_the_product_sent` runs the real
  frame against a real 4xx server, and fails against the old `{}`.
- `test_a_partial_run_says_so_in_the_line_gate_pl5_reads` — the scope note
  has to survive all the way from the runner into the criterion's own
  `detail`, because that string is what a kill decision is made on.
- `test_runs_predating_the_denominator_are_read_as_complete` — the new
  field must not retroactively put a caveat on runs 1–12.
- **Comparability:** runs 1–11 contain no crashed cases except run 4
  (already recorded as noise), so the floors' basis is unaffected. Run
  12's recorded 65% stands in the series as measured — it is a record,
  not a document — with the recomputed reading noted beside it in
  `HISTORY.md`. Runs from 13 on use the new denominators, and the metric's
  `changed_at` moves to 2026-08-12 so the framework's own rule flags any
  comparison straddling the break (F-22.1) rather than leaving it to
  whoever reads the table.
- Not fixed here, and stated rather than left implied: **why case 04's
  suite hangs is still unknown.** It is now a blocked task with a named
  reason instead of a dead run, which makes it diagnosable on the next
  run rather than diagnosed now.
