# ADR-054 — the criterion must survive being read

**Status:** accepted (2026-08-20)

**Answers:** "can you fix all issues found so far" — asked after ADR-053
shipped, which turned out to be the wrong question to answer from the
changelog. The issues were found by *running* the thing.

**Reverses:** nothing.

## Context

ADR-053 closed a defect class by auditing every aggregate in `src/` that
divides by a count. That audit read code. The next request was to close
whatever remained, so this time the check was to run the project's own
reporting commands against the real repository — `avs cadence`, then
`avs bench-criterion`.

`avs bench-criterion` printed its report and then crashed:

```
NameError: name 'streak_state' is not defined
```

Three separate defects sat in the twenty lines between the bench result files
and the human who reads them at Gate PL5. All three are the same shape as
ADR-051: a writer added something, and a reader that had *documented its
assumptions* was not updated with it.

### 1. The command crashed on the healthy path only

Below the `evaluate()` block sat ten orphaned lines from the implementation it
replaced, calling a `streak_state` that exists nowhere in the codebase. They
were unreachable in exactly one case — when the criterion *fires*, because
`raise typer.Exit(code=3)` sits above them.

So `avs bench-criterion` raised `NameError` on every run where the project is
healthy, and behaved correctly only in the case where it is not. Eleven
recorded runs, and the command that reads the launch PRD's only kill criterion
had never once completed successfully.

Nothing caught it because **no test invoked the command**. `evaluate()` has
coverage; the CLI path around it had none. One control, two call paths, and
the untested one silently did less — the ADR-051 sentence, again.

### 2. An aborted attempt counted as a run

`save_summary` writes `aborted:` *above* the rates, and says why in a comment:

> "four cases failed" and "this run never got to ask them" are different
> findings and the percentages look the same.

`bench_criterion.load_runs` — the one reader where that difference decides
something — never looked at the key. Run 17 died on credit exhaustion after
one case and sat in the capability ledger at build 100% over 1 of 5.

It was harmless only because it scored well. Invert it and the coupling is
the worst available: **an exhausted billing account advances a streak whose
consequence is a human being asked to consider killing the project.** ADR-052
made such a run resumable, which is the same statement in different words —
it is not final, so it is not a reading.

### 3. The ledger's stated ordering was broken by a filename

`load_runs`' docstring promises "every recorded run, oldest first, by filename
(they are timestamped)". That holds only while every name shares a prefix.
ADR-052 added `aborted-<date>-<reason>.yaml` beside `result-<date>.yaml`, and
`a` sorts before `r` — so the newest file on disk was placed at the *oldest*
position. Today that is invisible; it is what kept the run-17 abort out of the
two-run window, by luck rather than by rule.

The glob was `*.yaml`, so any file dropped in that tracked directory — a
notes file, a config — would be parsed as a capability reading.

## Decision

1. **Delete the orphaned block, and test the command.** `tests/
   test_the_criterion_reads_its_own_ledger.py` invokes it through `CliRunner`
   on both paths: exit 0 with no `NameError` when the criterion holds, exit 3
   with the Gate PL5 sentence when it fires. A script gating on this command
   gates on the exit code, so both codes are pinned.

2. **An aborted attempt is not a run of the series.** Excluded from the
   ledger by two independent guards: the glob (`result-*.yaml`) and the
   content check (`aborted:` in the payload). Two, because they catch
   different mistakes — the glob fixes the ordering, the content check
   survives an abort written under a `result-` name, which is what a future
   writer is most likely to get wrong.

3. **Excluded is not invisible.** `aborted-*.yaml` files are still walked and
   reported in `BenchCriterionState.aborted_skipped`, and the CLI names each
   one with the command that would finish it. A file the reader can see on
   disk and cannot find in the ledger is a reason to distrust the ledger.
   (This was a defect in the first draft of *this* record's own fix: narrowing
   the glob made the real run-17 abort vanish entirely rather than be named.)

4. **One scan, two filters.** `_scan` returns both lists from a single pass;
   `load_runs` and `aborted_runs` are views on it. Two functions each globbing
   the same directory would drift, and the thing they would drift about is
   which files the kill criterion counts.

5. **The cadence names the build that produced its numbers.** Its `state`
   column answers liveness in *days*, and days is a proxy that breaks exactly
   when releases outpace the cadence: the bench row read `ok (4d)` while its
   numbers came from v0.93.0 and the binary was v0.102.0 — nine releases. The
   result file has recorded `avs_version` since run 15, and the scheduler line
   already prints the running build, so the reading now carries `· measured on
   v0.93.0` and the comparison sits in front of the reader instead of in three
   documents they would have to go find.

## What stays out

**Any threshold on how stale is too stale.** Decision 5 states a fact the file
already contains; it does not judge it. `cadence` states and does not decide
(its rule 1), and a "bench reading is N releases old" alarm would fire on
every release and train the operator to scroll past the line that matters —
the reasoning `SchedulerBuild.behind` already uses for the trigger.

**Rewriting the recorded numbers of run 17.** The abort file stays exactly as
written. What changes is who reads it as a capability measurement.

**The floors and the streak.** `BUILD_FLOOR`, `PROBE_FLOOR` and
`CONSECUTIVE_RUNS_TO_FIRE` are untouched for the second record running. This
changes which runs are *eligible* to be judged, never the judgement.

## What keeps this honest

`tests/test_the_criterion_reads_its_own_ledger.py`, 10 tests. **6 of the
first 8 fail against the deployed v0.102.0** as a control.

The two that pass are worth naming rather than counting: `test_a_fired_
criterion_still_exits_three` pins the half that always worked, and
`test_the_newest_run_is_last_even_beside_an_abort` passes on the old build
too — with three files and a two-run window the abort fell outside it anyway.
It is a regression guard on an invariant that held by accident, not a
demonstration of the bug. The abort-exclusion tests are what demonstrate it.

**Not claimed:** that any recorded rate changes. The run-17 abort was above
both floors, so removing it from the ledger moves no streak and no number.
What changes is that it cannot move one in future.

## The lesson worth keeping

ADR-053's audit was a careful read of every aggregate in `src/`, and it was
genuinely thorough — it found `loop_metrics.py`. It could not have found any
of these three, because none of them is visible in the module under review:
the crash is in the caller, the abort key is in the writer, the ordering is in
the filesystem.

**Running the command found in one invocation what reading the code could not
find in an audit.** The bench costs five hours and real money, which is why
this project reasons so carefully about when to run it — and that habit
quietly spread to commands that cost nothing at all.
