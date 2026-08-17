# ADR-053 — a rate over no cases is not a rate

**Status:** accepted (2026-08-17)

**Answers:** the question "is there a cheaper alternative to a full five-case
bench run" — and the defect that pricing it uncovered.

**Reverses:** nothing. It enforces ADR-035's rule one level above where
ADR-035 enforced it.

## Context

Run 17 costs about five hours of wall clock and real money. Run 16's four
build cases took 2128 + 387 + 4078 + 4099 s = 2.97h, and the increment case
added in ADR-049 is estimated at ~2h more. But those five hours buy two
unrelated things: a **fifth** point on a headline series that already has four
(runs 13–16), and the **first** reading of `gate_rate`, which has none. The
obvious economy is to buy the second alone — point `--cases-dir` at a
directory holding only the increment case, pay ~2h, and get the number that
cannot be inferred from anything.

That run is well-formed by construction. ADR-049 already scores the increment
axis on its own denominator and excludes it from the headline three. What it
is not is *safe*, and the reason is one line:

```python
def _avg(values: list[float | None]) -> float:
    measured = [v for v in values if v is not None]
    return sum(measured) / len(measured) if measured else 0.0
```

With an empty build axis, `build = []`, so all three headline rates were
recorded as `0.0`. Then:

- `bench_criterion.below_floor` is `build_rate < 0.60 or probe_pass_rate <
  0.50`, with no measured-check;
- `CONSECUTIVE_RUNS_TO_FIRE = 2`;
- `save_summary` dual-writes into `benchmarks/results/`
  automatically — which is exactly the directory `load_runs` scans.

So the cheap run would have entered the capability ledger below floor, leaving
the project **one run away** from firing a criterion whose consequence is a
recorded human decision at Gate PL5 about whether to continue — on the
strength of a run that never asked whether anything builds.

The reporting was worse than the scoring. `cadence._bench_rates` appends its
"over N of M cases" qualifier only when `measured < total`; here both are `0`,
`0 < 0` is false, and the sentence came out as a flat unqualified **"build 0%,
probes 0%"** — the most alarming available reading of a run that made no claim.

This is ADR-035's own defect. `CaseResult.build_rate` has returned `None` for
a case with no denominator since that record was written; `BenchSummary` was
typed `float` and flattened it back to a zero one level up. `gate_rate` was
already `float | None` and already documented *why* — "a case with no
`feature_expectations` is not a gate scoring 100%, it is a case that did not
ask the question." The headline three were the holdout.

It also has ADR-051's shape: not a rule that was never written, but a rule
enforced on the path everyone takes and absent from the path nobody had taken
yet. Nothing was wrong with any run 1–17, because every one of them had at
least one build case. The guarantee held because of the shape of the input,
not because anything checked.

## Decision

1. **`_avg` returns `None` for an empty set, and `BenchSummary`'s three
   headline rates are `float | None`.** The type is what permitted this: a
   non-optional float has no way to say "nothing to average", so it said zero.

2. **The saved file writes `null`, not an omitted key.** A key that is present
   and null says this run considered the rate and found no denominator. An
   absent key is indistinguishable from a file written before the field
   existed — and `load_runs` must keep reading those, since the tracked
   scoreboard holds reconstructions going back to run 4.

3. **Both file readers check for null explicitly.** `bench_criterion.load_runs`
   skipped a null rate already, but only because `float(None)` raises
   `TypeError` into a handler written for malformed files. That is accidental
   correctness — the behaviour is right and nothing states it, so nothing
   protects it. `cadence._bench_rates` was accidentally correct the same way.
   Both now test the value and say why.

4. **A run with no build axis is absent from the capability ledger, not
   present with a zero.** It made no claim a floor could judge. The criterion
   is about build capability; a run that measured none is silent on it, and
   silence is not a failing grade.

5. **A rate with no denominator prints as "not measured"** in the CLI table
   and in the alert — the idiom `gate_rate` already used four lines below the
   site that needed it.

## The rest of the class

ADR-048 found one inert gate and ADR-050 found the same shape in seven
tokenizers, so a single instance is not evidence that the instance is all
there is. Every aggregate in `src/` that divides by a count was checked.

**Already correct, and mostly correct on purpose:**

| site | why it is safe |
|---|---|
| `product/claim_lint.py` | `if not claims: return [empty_ledger]` before the ratio — an artifact with no ledger is an *issue*, not a 0% inference rate |
| `review_gate.py`, `product/voter_gate.py` | `len(fixtures) != 8` raises first, so the registration floor never divides by zero. The security rule "nothing unfixtured registers" is checked, not assumed |
| `experiment/two_stage.py` | `two_proportion_p` returns `1.0` when either `n == 0`, so a zero-sample arm can never be significant and its `rate` of 0.0 is never judged |
| `marketing/substantiation.py` | `if not claim_words: continue` (ADR-050 territory) |
| `marketing/spam_policy.py` | `if batch and len(thin) / len(batch) > ...` |
| `testing.py` mutation score | already `if total else None` |
| `maintenance/skills_registry.py` | a zero-norm cosine returning 0.0 means *no match*, the conservative direction, and a raw-overlap floor guards it besides |
| `sweep.py` | `action_rate` of 0.0 on an empty sweep sits beside `clean_pass=not chores`, which disambiguates it in the same record |
| `upstream/autopilot.py` cost block | gated on `runs_seen > 1`, so "typical run $0.00" is never printed from no runs — the defect the README already records from the Studio |

**One more instance, fixed here:** `product/loop_metrics.py`. Five outer-loop
metrics; `kill_rate` and `attention_cost_per_resolved_hypothesis` return None
on an empty denominator and *say why* — "there is no correct target — a stated
rate near zero over many loops is itself the finding". `evidence_quality_ratio`
and `hypothesis_resolution_rate`, in the same file, returned `0.0`. The second
is the sharper one: its own docstring reads "a loop that resolves nothing is a
ratchet", so 0.0 is written as an indictment, and a loop that has not opened a
hypothesis yet was collecting it. A near-zero rate can only be a finding if
zero-from-nothing cannot reach it. Both now return None.

So the class was handled correctly in nine places and incorrectly in three,
and the three are the two aggregate layers that sit *above* per-item code that
already got it right — `BenchSummary` over `CaseResult`, `LoopMetrics` over
its own siblings. That is the pattern worth remembering: the defect lives
where results are combined, not where they are computed.

## What stays out

**Any change to the floors, the streak length, or what `below_floor` means.**
`BUILD_FLOOR`, `PROBE_FLOOR` and `CONSECUTIVE_RUNS_TO_FIRE` are untouched.
This record changes which runs are *eligible* to be judged, never the
judgement.

**A `--only <case>` flag.** Selecting one case needs no code — `--cases-dir`
pointed at a directory with one file already does it. A flag would be a second
way to express the same thing, which is the ADR-051 failure mode.

**Whether the cheap run should be run.** This makes it safe; it does not
decide it.

## What keeps this honest

`tests/test_empty_axis_is_not_a_zero.py`, 13 tests in three groups, and the
second group is the one that matters — a fix that blinded the criterion would
be worse than the defect it replaced.

- *An empty axis* — `test_an_empty_build_axis_scores_no_build_rate`,
  `test_the_saved_file_says_null_not_zero` (asserts present-and-null, not
  absent), `test_a_run_with_no_build_axis_never_reaches_the_floor` (asserts
  `runs_considered == []`, not merely that nothing fired),
  `test_the_cadence_reports_nothing_rather_than_zero`,
  `test_neither_the_table_nor_the_alert_prints_a_percent`.
- *A real zero is still a zero* — `test_a_case_that_built_nothing_still_scores_zero`
  keeps ADR-035's other half (`tasks_total == 0` is a measured total failure,
  not an absence); `test_a_real_zero_still_fires_the_floor` asserts
  `streak == 1` on a genuine 0%; `test_a_mixed_run_keeps_the_build_rate_it_earned`
  pins the ordinary five-case shape, where an increment case sits beside build
  cases and must not turn the headline rates into `None`;
  `test_an_all_unmeasured_build_axis_is_absent_not_zero` covers the other
  route to an empty average — every build case crashed.
- *The rest of the class* — the loop-metrics find gets both halves too:
  `test_a_stage_with_no_claims_has_no_evidence_quality`,
  `test_a_loop_with_no_hypotheses_is_not_a_ratchet`,
  `test_the_metrics_object_carries_the_nulls` (the field types are half the
  fix here as they were for `BenchSummary`, and it pins the two metrics that
  were already right alongside), and
  `test_a_populated_ledger_still_scores_what_it_earned`, which keeps a real
  all-weak ledger reading 0.0 and a real unresolved hypothesis reading 0.0.

Run against the deployed 0.101.0 as a control: **5 of 9 fail**. The 4 that
pass are the entire "a real zero is still a zero" group plus the cadence
check, and they should — three assert a property that held before this change
and must still hold after it, and the fourth was the accidental correctness
decision 3 makes explicit.

**Not claimed:** that any recorded run's numbers change. Every run 1–17 had at
least one build case, so no historical rate moves. What changes is that the
cheaper run is now a measurement instead of a hazard.
