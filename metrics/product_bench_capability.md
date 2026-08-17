---
metric:
  id: product_bench_build_rate
  definition: "fraction of product-bench cases whose generated product BUILDS (every planned task reaching built with its suite green) in one run; companion series in the same run are the probe pass rate and the clean-review rate"
  numerator_event: product_bench.case_built
  denominator: "cases in the run's corpus (benchmarks/products-real/) that RAN — a case dropped by harness noise is excluded from every rate alike, a case that ran and produced nothing scores a real 0.0; each result YAML records cases_measured / cases_total / unmeasured (ADR-035, ADR-043)"
  window_days: 7
  cohort_basis: bench_run
  exclusions: ["cases that died on harness noise rather than on the product under test (run 4 KeyError, run 5 budget exhaustion, run 12 pytest timeout) — enforced mechanically since v0.83.0, stated here since 2026-07-27", "runs whose corpus differs — comparability resets when a case is added or edited"]
  owner: melody
  changed_at: "2026-08-17"
---

# product-bench capability (build / probes / clean)

## Why this is a kill-criterion axis

This axis asks the question that comes before affordability: **does it
still work?** A build rate that collapses means the product loop is
generating things that do not run, and no discipline elsewhere makes that
worth continuing.

Its practical advantage — and since v0.81.0 the reason it is the *only*
axis — is that **the series already exists**. `benchmarks/results/HISTORY.md`
carries runs 4–11 with a result YAML per run, and the cadence is weekly, so
this criterion can fire on the next run without anyone being asked anything.
The other axis measured weekly maintenance hours a person had to type in;
after three weeks it had collected none, and it was withdrawn with the loop
that collected it (ADR-033).

## The observed distribution (what makes the floors real)

| era | build | probes | clean |
|---|---|---|---|
| runs 4–5 (pre-fix) | 8–33% | 0% | 0–17% |
| runs 6–9 (fixes landing) | 42–72% | 23–90% | 0–41% |
| runs 10–11 (current) | 74–75% | 75% | 38–43% |

The kill criterion's 60% build / 50% probe floors sit **below the current
level and above the pre-fix era**. They are read off this table, not chosen
to be comfortable: crossing them means the system has regressed into
territory it has already climbed out of once.

## How it is evaluated

`avs bench-criterion` reads the tracked result YAMLs, takes the two
most recent comparable runs, and reports whether the criterion has fired. It
never rewrites history and never averages across a corpus change. A fired
criterion demands a recorded human decision at Gate PL5 (invariant 14.20) —
the evaluator states, it does not decide.

## The denominator (2026-08-12 definition change, ADR-035)

The exclusion above — *cases that died on harness noise rather than on the
product under test* — was written into this file on 2026-07-27 and was not
true of the code. `_run_product_bench` averaged a `0.0` for any case with no
denominator, so run 12's case 04, killed by a `pytest -q` that never
returned, entered all three rates as a zero and cost 22 points of probe
rate. **A stated exclusion that nothing enforces is a comment.** Since
v0.83.0 the runner enforces it, and every result records
`cases_measured` / `cases_total` / `unmeasured` so a reader can tell
75%-of-four from 75%-of-three. `avs bench-criterion` prints
`(over 3 of 4 cases)` when that is what happened.

Two things this deliberately does **not** do. A case that ran and built
nothing still scores a real `0.0` — that is a failure, not an absence, and
the exclusion must never become a way to drop bad runs. And run 12's
recorded numbers are not rewritten; `benchmarks/results/` is a record, with
the recomputed reading noted beside it in `HISTORY.md`.

`changed_at` moved to 2026-08-12 because the denominator changed, so
comparisons straddling it are flagged (F-22.1). The floors are unaffected:
runs 1–11 hold no crashed cases except run 4, already excluded as noise.

## The denominator, second correction (2026-08-16 definition change, ADR-043)

The paragraph above says a case that ran and built nothing still scores a
real `0.0`. **The code did not do that**, and run 16 is where it showed:
`02-shortener-api` ran for 6.4 minutes, came back with no tasks, and was
dropped from the build rate — which reported 100% while one of four cases
had produced no product at all. The same case's probes were kept and
averaged in as 0.0, because the exclusion was decided per *rate* rather
than per *case*, so one summary excluded a case from two rates and counted
it in the third.

Since v0.94.0 `measured` is one decision on the case (`not
autopilot_status.startswith("error")`), every rate reads it, and zero tasks
is the most complete build failure available rather than an absence of
measurement. The clean-review rate is the one exception and stays `None`
when nothing was built: it has no denominator to be a zero *of*, and
entering it as 0.0 would charge the same failure twice.

**The build rate's comparability breaks here.** Runs 4–16 measured cases
that got a plan; run 17 is the first that measures cases that were asked
for a product. Run 16 read honestly under the new definition is `build 75%
· probes 75% · clean 31%` over 4 of 4 — its recorded numbers are not
rewritten. The 60% floor is read off runs 10–11, which hold no
produced-nothing cases, so it is unaffected in level; but a run 17 build
rate is not directly comparable to run 16's recorded 100%.

## The clean rate's comparability breaks here too (ADR-044)

Six of run 16's eleven rejections were the repair pass failing, not the
product: the reviewer's most common request — hoist duplicated test
boilerplate into a shared helper — was impossible to perform, because
`assertion_delta` judged one file at a time and read every assert moving
into the helper as a deletion. The rewritten call sites were dropped, the
new helper was kept, and the half-change was committed; the re-review then
flagged the orphan the pass had just created. Run 17 is the first run in
which that repair can land, so its clean rate is not comparable with runs
13–16 in either direction — the earlier numbers measured a pass that could
not perform its most-requested fix.

This does not move the kill criterion, which is read off build and probes.
It is recorded here because the clean rate is the number this metric is most
often asked about, and because run 16's evidence for it was deleted by the
preservation rule — now fixed, so run 17's rejections keep their workspaces.

## Falsifier

Two consecutive weekly runs at or above 60% build and 50% probes falsify the
regression claim for that window. A single good run does not: the whole point
of "2 consecutive" is that one lucky run is noise at this corpus size (n=4
real-product cases).
