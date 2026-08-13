# ADR-034 — The kill criterion's series becomes a watched loop; its schedule leaves cron

- **Status:** accepted, 2026-08-12 (operator decision)
- **Reverses:** the weekly product-bench schedule installed 2026-07-27 as a
  crontab entry (`7 9 * * 1 weekly-product-bench.sh`) — an unversioned shell
  script on an unwatched timer, outside the framework it measured
- **Does not reverse:** the capability criterion itself (**O-L2**,
  `bench_criterion.py`, floors 60/50 over 2 consecutive runs), the compound
  and sweep loops, the daily product-workspace agent, or ADR-033's rule that
  every scheduled loop must be able to close itself

## Context

ADR-033 withdrew the maintenance-attention axis three weeks after launch,
leaving the product-bench capability axis as the launch PRD's **only** kill
criterion. Its whole justification was that its series already existed and
was collected mechanically: `benchmarks/results/*.yaml`, one file per weekly
run, no human asked anything.

The series had stopped. The newest result was `result-2026-07-27-0449.yaml`
— run 11, sixteen days and three scheduled Mondays earlier. Nothing had
complained, because nothing was watching. Two independent defects, either
sufficient:

1. **The trigger never fired.** cron *skips* a job whose minute passed while
   the Mac was asleep; it does not defer it. `~/Downloads/autoproduct-weekly-bench.log`
   holds exactly one entry, ever — the preflight run of the day it was
   installed.
2. **It could not have authenticated if it had.** The script sourced
   `^export (ANTHROPIC|OPENAI)_API_KEY=` out of `.zshrc`. The v0.71.1
   LaunchAgent hardening had since converted that variable to its
   `ANTHROPIC_API_KEY_FILE` form — a change made for good reasons in a
   different file — so the pattern matched only the OpenAI line.

The structural fault is neither of those. It is that `avs cadence` watched
`compound` and `sweep` and did not watch the one series a kill criterion
actually reads. A criterion whose series has silently stopped reports "not
fired" forever, and reads exactly like a criterion that is being satisfied.
That is the same failure class ADR-033 removed, one directory over: the
absence of a reading rendered as a clean pass.

## Decision

1. **The bench is a loop in `cadence.py`**, on the same seven-day cadence
   and read the same way — the newest ISO date embedded in
   `benchmarks/results/result-*.yaml`. Overdue is a finding, `never_run` is
   `never_run`, and both reach Discord through the channel v0.80.0 built.
2. **It is tracked only where its cases live.** No
   `benchmarks/products-real/`, no bench loop. The bench measures the
   *framework's* capability against four labelled real products that ship in
   this repository; reporting it as overdue in every product workspace would
   put a standing false alarm in the one channel that must not cry wolf.
3. **It runs itself.** `avs cadence --run-due` invokes
   `avs product-bench --cases-dir benchmarks/products-real`, with the cases
   directory named explicitly — the command's default is the synthetic set,
   and the criterion is defined over the real one. It is a paid, hour-long
   run, and per-loop timeouts exist so an hour-long run is not killed at
   fifty-nine minutes and reported as a capability failure. ADR-033's rule
   holds: this is a run, not a question.
4. **The schedule moves to launchd, in this checkout, under its own label.**
   `avs cadence --only bench --label ai.venture.studio.bench --install
   --notify` writes a second agent. launchd runs a missed job on wake. The
   crontab entry is removed; `weekly-product-bench.sh` survives as a manual
   clone-and-push variant with its credential sourcing fixed, and says at the
   top that it is no longer scheduled.

## What keeps this honest

`--only` is validated against the loop names the module knows, not against
what happens to be present, and naming a loop the workspace does not have is
an error rather than an empty report. Both matter for the same reason: a
filter that quietly selects nothing produces a scheduler that watches
nothing and reports all clear every morning — this bug, re-entered through a
typo.

The tests pin the incident rather than the code:
`test_the_bench_series_going_quiet_is_now_a_finding` reconstructs the real
dates (cases present, last result 2026-07-27, asked on 2026-08-12) and
asserts `overdue`; `test_a_workspace_without_the_cases_is_not_told_it_owes_a_bench`
pins the other half; `test_the_bench_closes_itself_like_every_other_loop`
carries ADR-033's rule forward to the loop added after it.

The honest cost: the scheduled run no longer commits and pushes the result,
which the shell script did. The criterion reads the working tree, so the
axis works regardless — but the series survives losing this machine only if
someone commits the file, and that is now a line in the weekly rhythm rather
than something automatic. It is the deliberate trade for having the runner
be versioned, tested, and watched instead of a script in `~/.local/bin` that
nothing pinned and nothing checked.

Sixteen days is the measured cost of the gap this closes. Nothing detected
it; it was found by reading the directory while answering a question about
remaining work.
