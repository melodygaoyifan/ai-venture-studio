# ADR-052 — a measurement is bought once

**Status:** accepted (2026-08-17)

**Answers:** run 17, which measured one case over 3438 seconds of real spend,
lost its account, and left nothing reusable behind.

**Reverses:** nothing. It extends a rule the system already keeps at three
other levels down to the one unit that never had it.

## Context

Run 17 (2026-08-17, `benchmarks/results/aborted-2026-08-17-1412-credit-
exhausted.yaml`) fired early against the deployed 0.97.0. Case 01 completed —
4/4 built, 3/3 probes, 3438 seconds. The account then ran out of credit during
case 02, which spent a further 1541 seconds before dying, and cases 03, 04 and
05 each rediscovered the same fact in 0.3s. The result: `cases_measured: 1` of
5, and one measured hour that no later run can use.

Three separate defects, none of which is about the cases:

1. **Nothing is banked until everything is.** `save_summary` is called once,
   from the CLI, after the loop returns. Run 17 kept its case-01 row only
   because the loop happened to reach the end. ADR-036 kills the whole process
   group at `BENCH_TIMEOUT_S = 8h`; run 16 already used 2.97h of that budget
   and a fifth case was added afterwards, so the timeout is a live possibility
   and it destroys every finished case with it.

2. **An environment failure is handled as a case failure.**
   `_run_product_bench`'s handler is commented *"one case never kills the
   bench"* — correct for a hung suite or a 529 that outlived its retries, and
   wrong for an account with no credit, which will kill every case that
   follows. The loop marched through four more cases to record four identical
   errors.

3. **Nothing checks the account before the run.** The cheapest possible signal
   — one token — was never asked for, and the run found out an hour and a half
   in.

The rest of this system already knows (1). `autopilot._todo_and_skipped` skips
tasks that were already built; `deploy.score_node` is *"the expensive
super-step a resume must never re-pay when it already completed"*;
`cli.py:1837` puts it plainest — *"a resumed run would rebuild these and charge
you again."* A bench case is the most expensive unit in the system and the only
one with no such rule.

## Decision

1. **A finished case is written to `.mas/product-bench/checkpoints/` before the
   next case starts**, not at the end of the run. The next case is what takes
   the process down; everything measured before it is worth more than the run
   still in progress. Write-then-rename, so a kill mid-write leaves no
   half-file for the next resume to read.

2. **Only measured cases are banked.** A crashed case is precisely the one a
   resume must retry — banking it would make a transient 529 permanent and
   turn it into a case this bench never measures again.

3. **`--resume` reuses a banked row only when its key still matches**: case
   name, a digest of the case file's full content, `avs_version`, and provider.
   `autopilot._todo_and_skipped` keys on `(task_id, title)` rather than the id
   for exactly this reason, and says why: *"skipping work is only safe when we
   can say what work it was."* Every rejection path — missing, unreadable,
   undateable, stale, mismatched — ends in the case running, which is what
   would have happened anyway. Without `--resume`, checkpoints are never read
   at all, so a run nobody asked to resume is the run it has always been.

   A resume also reaches back no further than 14 days. The key already refuses
   another build or another version of the case file, so an older row is not
   *wrong* — but "the same build and the same case, three weeks ago" is a claim
   about a machine nobody has run since, and a scoreboard should not be able to
   quietly assemble itself out of last month. **The bound is a read rule, not a
   cleanup pass.** Deleting inside `.mas/` is the one thing this repo does not
   do: it holds unrecoverable run history and forensics, and it was wiped once
   (2026-07-26, runs 1–8's originals lost). Refusing to read a stale checkpoint
   gets the entire benefit and destroys nothing — the file stays for whoever is
   diagnosing the run it came from.

4. **A resumed row says so**, in the table, in the row (`resumed: true`) and in
   the result file's `rates.resumed` list. Every number in a reused row is
   real, which is exactly what would hide it: a scoreboard that cannot
   distinguish measured from read-off-disk claims work it did not do — the
   ADR-035 failure with better camouflage.

5. **A terminal environment failure aborts the run**, and the untried cases are
   recorded as unmeasured under one shared reason rather than re-derived one at
   a time. Two independent detectors:

   - a table of provider wording (`credit balance is too low`, quota, auth) and
     statuses 401/402/403 — the fast path, pinned in a test against the exact
     string from the aborted run;
   - **two consecutive cases failing with an identical type and message** — the
     backstop, which needs no vocabulary. The table stops firing the day the
     provider rewords its errors and nothing would say so; the streak detector
     survives that, and is the reason the table is allowed to be a table.

   429 is deliberately excluded: it is transient, the provider adapter already
   retries it six times with backoff, and one that reaches here has outlived a
   real overload event.

6. **A preflight spends one token before the run spends hours.** It lives in
   the CLI command, not in `run_product_bench`: the library function is what
   the hermetic suite drives, and a network call inside it would either break
   that or need a flag defaulting to off — the ADR-051 shape exactly, a guard
   wired into the path nobody takes. An unrecognised error is swallowed,
   because this is a preflight and not a gate; refusing a three-hour run over
   an unrecognised blip would be the check causing the outage.

## What stays out

**Within-case resume.** A case either completed or it runs again. The
workspace is a `mkdtemp` deleted in a `finally`, the autopilot's own resume
operates on a git repo that no longer exists by then, and a partly-measured
case is not a measurement. Only the result row is reusable.

**Cross-build reuse of any kind.** Decision 3 refuses it rather than offering
an override. Reusing a row measured on 0.97.0 inside a run of 0.100.0 averages
two machines into one scoreboard — the confound ADR-049 narrowed `cases_total`
to prevent, arriving through the optimisation meant to save money.

**`--resume` in the cadence command.** `cadence._bench_status` still emits
`avs product-bench --cases-dir <dir>` with no `--resume`, and that is
deliberate. The weekly series is supposed to be a fresh reading; a scheduled
run that silently reused last week's rows would publish a result for a week
nobody measured, which is the ADR-035 failure again with a longer lever.
Resume is an operator's recovery tool for a run that died, not a scheduling
policy.

**Deciding what the bench runs against.** This ADR makes a re-run cheap; it
does not touch the standing question of which build is installed when the
cadence fires.

## What keeps this honest

`tests/test_bench_resume.py`, 29 tests in three groups that fail
independently:

- *Banking* — `test_a_finished_case_is_on_disk_before_the_next_one_runs`
  asserts what exists on disk at the moment case 2 **starts**, not at the end
  of the run, which is the only formulation that would have saved run 17.
  `test_a_crashed_case_is_never_banked`,
  `test_a_bank_that_fails_does_not_lose_the_run`.
- *Reuse* — `test_a_row_from_another_build_is_refused` and
  `test_an_edited_case_is_refused` pin the key;
  `test_a_resumed_row_says_it_was_resumed` checks the table, the row and the
  saved file; `test_a_run_without_resume_measures_everything` pins the default;
  `test_a_corrupt_checkpoint_is_no_checkpoint` and
  `test_a_checkpoint_that_cannot_date_itself_is_refused` pin the rejection
  direction; `test_a_stale_checkpoint_is_refused_but_not_deleted` asserts both
  halves of the age bound, including that the file survives being ignored.
- *Aborting* — `test_the_real_credit_error_is_recognised` is pinned against the
  verbatim string in the aborted result file.
  `test_two_identical_failures_abort_without_any_vocabulary` covers the
  backstop with no marker present at all, so the two detectors cannot rot
  together. `test_a_rate_limit_is_not_environmental`,
  `test_an_ordinary_case_crash_is_not_environmental`,
  `test_two_DIFFERENT_failures_do_not_abort` and
  `test_a_success_between_two_failures_resets_the_streak` are the negative
  half: a false abort is a new way to lose measurement, which is what this
  record exists to stop.

Run against the previously released build as a control: 26 of 27 fail there.
The one that passes is
`test_the_cases_that_were_never_asked_are_unmeasured_not_failures`, and it
should — it asserts ADR-035's property, which held before this change and must
still hold after it. Aborting early moves *when* the untried cases are
recorded, never *how* they are scored.

Verified end to end against the mock provider: a one-case run measured in
48.8s, and the same run with `--resume` returned the identical row in 0.62s,
marked `(resumed)`.

**Not claimed:** that any rate moves, or that a re-run of run 17 is now free —
only that the part already paid for is not paid for twice, and that the run
stops at the first failure that was never about a case.
