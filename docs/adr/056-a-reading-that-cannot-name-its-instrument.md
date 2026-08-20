# ADR-056 — a reading that cannot name its instrument

**Status:** accepted (2026-08-20)

**Answers:** "is there anyway we can validate code without run batch 17" —
asked while run 17 is blocked on API credit, and answered by finding that the
cheapest available substitute quietly corrupts the thing it substitutes for.

**Reverses:** nothing. It narrows what may enter `benchmarks/results/`, which
ADR-054 had already narrowed once along a different axis.

## Context

Run 17 costs roughly five hours and real API spend, and the account is out of
credit. The obvious question is what can be validated without it, and the
obvious first answer is `avs product-bench --provider mock` — a documented
option on that exact command, free, offline, deterministic.

Before running it, one check: ADR-054 had just established that
`benchmarks/results/` is the ledger the capability kill criterion reads, and
that a file which does not belong in the series must be excluded *and named*.
So — can a mock run get in?

It can. Demonstrated rather than reasoned about, per ADR-054:

```
provider='mock'  tracked=['result-2026-08-20-0825.yaml']
counted as capability readings: [('benchmarks/results/result-...yaml', 1.0)]
excluded/reported: []
```

`save_summary` dual-writes every result into the tracked directory
unconditionally. `BenchSummary` has no provider field, and the payload — which
takes the trouble to record `avs_version`, `cases_measured`, `cases_total`,
`unmeasured`, `resumed` and `aborted`, each for a stated reason about what a
later reader would otherwise be unable to tell — does not record which
provider produced the numbers. `bench_criterion._scan` globs `result-*.yaml`
and has nothing to filter on.

So the file is byte-identical in shape to a real reading, and every downstream
consumer treats it as one: `evaluate()` counts it in the streak, `movement()`
diffs the next real run against it, `concern()` reports on it, and `cadence`
tells a human what it says.

This is ADR-054's shape aimed at the opposite failure. There, the criterion
crashed rather than reporting. Here it reports confidently — about a regex
table. `providers/mock.py` says so in its own first line: *"Deterministic
provider for tests and offline runs."* Its answers are pattern matches over
the diff. A build rate measured against it is a fact about the fixture set.

The coupling is what makes it worth a record rather than a patch. The numbers
in that directory are the sole input to the launch PRD's only kill criterion,
whose output is *a human being asked to decide whether to kill the project*
(Gate PL5, invariant 14.20). A free, encouraged, offline command could write
into it, and — depending only on how the mock happened to score — could either
mask two genuinely bad runs or advance the streak toward that question.

Nobody had run it, so nothing was corrupted. It was safe by accident.

## Decision

**A result file records the provider that produced it, and a simulated
provider does not produce capability readings.**

1. **`providers/base.py` names which registry members are not measuring
   instruments** — `SIMULATED_PROVIDERS = frozenset({"mock"})` and
   `is_simulated()`. One definition, because a second copy is a second
   definition of "real run" and the thing the two would drift about is which
   files decide whether that question gets asked (ADR-038, ADR-051). A test
   asserts the name appears in exactly one file in `src/`.

2. **`save_summary` records `provider:` on every run**, beside `avs_version`
   and for the same reason that field exists: a row that cannot name what
   produced it cannot be compared to the row above it. On *every* run, not
   only simulated ones — a field that appears exactly when something is wrong
   is a field nobody thinks to look for.

3. **A simulated run is not dual-written into `benchmarks/results/`.** It
   still writes `.mas/product-bench/`, because a mock run *is* a real exercise
   of the harness and its scoreboard is the output of that check.

4. **`_scan` refuses to count a simulated result that reaches the directory
   anyway** — hand-copied, restored from a backup, written by a build from
   before this change — and reports it, the rule the aborted-run list already
   established: a file a reader can see in the directory and cannot find in
   the ledger is a reason to distrust the ledger. Two layers, because each is
   silent about the other's case.

5. **An absent `provider:` key is read as real.** Every result written before
   v0.105.0 lacks the field and every one of them was a genuine run against
   anthropic. Guessing the other way would silently delete eleven capability
   readings from the criterion's view, and it would then report — correctly,
   uselessly — that there is no data. `is_simulated(None)` is `False` for the
   same reason: `None` means "the default", which is real.

6. **The command says so at the moment the numbers are on screen**, rather
   than leaving it to whoever opens the file later: *"provider 'mock' is
   simulated — these rates measure the harness, not the system."*

7. **The other two readers of that directory are closed with it**, because
   the criterion was never the only one. `cadence._bench_status` globs
   `result-*.yaml` itself to answer "has the bench run lately"; a simulated
   file would have made the watchdog report *ran today, all clear* about a run
   that read nothing — the failure its own `LOOP_NAMES` comment says a
   watchdog must never commit. It now asks `bench_criterion.simulated_runs`
   rather than re-deriving the rule, since two readers of one directory drift
   and what they would drift about is which files count. And `bench_alert` —
   sent even on a clean run, because a bench result is what somebody is
   waiting on — marks a simulated run in the *heading*, which is the part a
   phone notification shows.

## What stays out

- **Refusing `--provider mock` on `product-bench`.** It is the right way to
  check that the harness runs end to end without spending five hours and an
  API budget, which is the question that prompted this. The defect was never
  that the run happens; it was that its output was indistinguishable from a
  measurement.
- **Backfilling `provider: anthropic` into the eleven existing files.** They
  are the recorded series. Rule 5 reads them correctly without editing them,
  and rewriting a ledger to match a new reader's expectations is the habit
  this project keeps writing ADRs against.
- **Treating `openai`, `xai` or `google` as simulated.** They are real models
  answering real prompts. A run against one measures the system on a different
  model, which is a comparability problem for whoever reads the series — the
  `provider:` field now makes it visible — not a fabrication.
- **A floor, threshold or verdict on mock runs.** `bench_criterion.concern`
  is explicit that the floors live in one place; adding a second axis of
  judgement here would be the back-door change it warns about.

## What keeps this honest

`tests/test_a_reading_that_cannot_name_its_instrument.py`, and the ones that
matter are the negative directions:

- a real run **still** enters the tracked ledger (the half that is easy to
  break and silent when broken);
- a legacy file with no `provider:` key is **still** read as a run;
- the actual `benchmarks/results/` shipped in this repo still reads as a
  series of ≥10 runs with zero exclusions — the compatibility rule checked
  against the files it was written for, not against a fixture;
- `cadence._bench_status` reports `never_run` rather than "ran today" when the
  only file present is simulated;
- a real run's alert is unchanged, for `provider=None` and `"anthropic"` both;
- `SIMULATED_PROVIDERS` appears in exactly one module.

And the fix was demonstrated the way the defect was — by running the command.
`avs product-bench --provider mock --limit 1` against a scratch repo, with
every provider key unset: 40.5s, one case, autopilot completed, 3/3 built,
2/2 probes, 3/3 clean reviews, result in `.mas/`, `benchmarks/results/` empty,
and the warning on screen.

## The lesson worth keeping

ADR-054 said the criterion must survive being read. This is the next question
along: **the criterion must be able to say what it read.**

Every field that payload records — the version, the denominators, the
unmeasured list, the resumed list, the abort — was added after someone
discovered they could not tell two different runs apart from the file. The
provider is the same discovery, found before it cost anything, because the
cheap substitute for an expensive measurement was examined for whether it
could be mistaken for the measurement.

There is a second half to the answer that prompted this, and it is not a
defect: **"validate" names two different things.** Whether the *harness* works
is free, and is where every recent defect actually lived — ADR-052's
checkpoint loss, ADR-053's empty-axis rates, ADR-054's crashing reader,
ADR-055's undefined names. Not one of them needed a provider. Whether the
*system is capable* — build ≥ 60%, probes ≥ 50% — cannot be substituted at
all, because a capability number measured against a mock is a measurement of
the mock. The first is now safe to run. The second still requires run 17.
