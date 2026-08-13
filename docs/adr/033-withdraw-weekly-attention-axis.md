# ADR-033 — Withdraw the weekly maintenance-attention axis; the scheduler asks nobody anything

- **Status:** accepted, 2026-08-12 (operator decision)
- **Reverses:** the weekly attention mechanism shipped across v0.42.0–v0.50.0
  — `avs attention`, `metrics/attention-log.yaml`, launch PRD outcome **O-L1**
  (`weekly_maintenance_attention_hours`) and the kill criterion built on it,
  the `attention` cadence loop, `LoopStatus.human_input_required`, and the
  cycle report's attention axis
- **Does not reverse:** the capability kill criterion (**O-L2**,
  `bench_criterion.py`), the compound and sweep loops, the daily scheduler
  itself, the Discord error channel (v0.80.0), or the recorded Gate PL5
  evaluation of 2026-07-26 — that snapshot stands verbatim with a
  `superseded_by` note appended beside it

## Context

The launch PRD had two kill criteria. The first said: if the framework's own
weekly maintenance attention exceeds 4.0 hours for 4 consecutive weeks, cut
scope at Gate PL5. It was authored pre-launch, honestly, and the machinery
around it was careful — `avs attention` refused to invent a number, logged
`not_tracked` rather than estimate, and the cadence watchdog was built so the
machine could never answer on the operator's behalf.

The care was real and the axis still did not work, for a reason no amount of
care fixes: **its only instrument was a person typing a number every week.**
Three weeks after launch the log held one `not_tracked` row and zero logged
hours. The criterion could not fire, and — as the Gate PL5 record said
plainly — it could not be declared safe either. What the series actually
measured was willingness to answer a weekly prompt, not maintenance load.

It also cost more than nothing. The `attention` loop was due every seven days
and exited non-zero every single morning by design, because "not yet
answered" is its normal state. That forced a permanent exemption in the alert
path: the one loop guaranteed to fail daily had to be excluded from the error
channel or the channel would cry wolf every morning. A scheduled job whose
non-zero exit means nothing is a hole in the error reporting, and it existed
solely to carry this axis.

Against the founder this system is built for — as lazy and as non-technical
as possible, for whom typing is the most expensive thing the product can ask
— an axis whose upkeep is a weekly typed number was never going to hold.

## Decision

1. **The mechanism is removed entirely.** `attention.py`, `tests/
   test_attention.py`, `metrics/attention-log.yaml`, and
   `metrics/weekly_maintenance_attention.md` are deleted. The `avs attention`
   command is gone.
2. **The PRD is amended, not quietly trimmed.** Outcome O-L1 and its kill
   criterion are withdrawn from `launch/prd.yaml` with the withdrawal recorded
   in place; the id O-L1 is not reused. One axis remains — the capability one,
   whose series `benchmarks/results/*.yaml` is already collected mechanically
   and can fire on the next run without asking anyone anything.
3. **The Gate PL5 record is not rewritten.** Its 2026-07-26 reading stands
   verbatim; a `superseded_by` block is appended pointing here. The criterion
   was *withdrawn, not satisfied*, and the record has to say which.
4. **Every loop the scheduler drives can now close itself.** With the human
   loop gone, `LoopStatus.human_input_required` and the alert's "no machine
   can answer this" branch are removed — and so is the exemption in
   `notify._failures`. A non-zero exit from any scheduled loop is now a
   failure, full stop, and reaches the Discord channel.

## What keeps this honest

The kill criterion that remains is the one that can actually fire. Dropping
an axis that never produced a reading is not the same as dropping oversight,
and the difference is visible in the artifacts: O-L2's instrumentation
`exists: true` and its floors were read off the observed distribution, not
chosen.

The removal is pinned by tests as firmly as the presence was —
`test_the_cycle_no_longer_reads_an_attention_log`,
`test_the_reader_module_is_gone`,
`test_every_loop_the_scheduler_drives_can_close_itself`,
`test_no_loop_claims_a_person_has_to_close_it`, and
`test_a_non_zero_exit_is_a_failure_with_no_exceptions`.

The honest cost of this decision: the framework no longer measures its own
maintenance burden at all. That is the correct trade only because the
measurement it replaced was not happening. If a burden signal is wanted
again, it has to come from something already timestamped — commits, gate
decisions, loop runs — and not from a prompt.

A reversal recorded only in a commit message would be indistinguishable from
scope drift, which is why this document exists (§10 Part 11: the newest
accepted decision wins and must be recorded).
