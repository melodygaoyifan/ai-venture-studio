# The published benchmark (doc 25 §74)

product-bench is the public trust artifact: four workspaces with seeded
defects and pinned expected findings, runnable by anyone with one API key.
**A benchmark you can only pass is marketing; one you can fail in public is
evidence** — regressions publish too, because `eval-gate` baselines live
in-repo and a version that drops recall shows in the diff.

## Reporting rules (claim-lint semantics, applied to ourselves)

- Every number carries model ID, date, harness version, and run count;
  single-run numbers are labeled **n=1** and never headline.
- Catch-rate claims come only from seeded-defect manifests
  (`avs toolchain --manifest`); uncalibrated lanes publish
  **PROVISIONAL** in the same font size.
- No cross-framework comparison tables: comparing our recall to another
  framework's would require running *their* harness at equal effort, which
  we have not done. The honest form is "here is ours, runnable."
- Every figure below resolves against [claims/platform.yaml](../claims/platform.yaml);
  the hermetic suite fails if this page asserts beyond the ledger (ADR-U29).

## Review benchmark — 13 labeled cases

recall 100%, precision 67% (bars: 40% / 50%) · `avs bench` ·
harness v0.12.0+, 2026-07-22. Cases: planted SQL injection, missing-WHERE,
swallowed exceptions, eval-on-input, hardcoded secrets, typosquat deps,
CSRF/SSRF, plus clean-diff controls and three real-bug regressions.

## Product benchmark — full FDR → product runs

Scored by independent behavioral probes executed against the built product
(WebGen-Bench pattern); build rate, probe pass rate, and clean-review rate
reported unaveraged.

| Case set | build | probe pass | clean review | run |
|---|---|---|---|---|
| synthetic (3 cases incl. the honesty case) | 100% | 83.3% | 100% | n=1, 2026-07-23, claude-opus-4-8 writer |
| real (4 cases, plain-Chinese FDRs) | 33% | 0% | 17% | n=1, 2026-07-26, run 5 — the pre-fix baseline; fixes landed after, re-run pending |

The real-case row is the honest one and stays published: the probes are
independent and they failed. The synthetic honesty case exists to prove
probes *can* fail (one probe demands the impossible).

## Reproduce

```bash
uvx avs replay --demo        # no key: a real review's audit trail
avs bench                    # ~10 min, one key: the 13-case review bench
avs product-bench            # long: full FDR→product runs
avs init demo --profile web --from-bench 01-groupbuy-api   # templates ARE the fixtures
```

To check that the harness works without spending anything, run it against the
simulated provider — no key, offline, about a minute per case:

```bash
avs product-bench --provider mock --repo-dir /tmp/scratch
```

That exercises the whole path — autopilot, build tasks, independent probes,
review passes, checkpointing, the result file — and it is where recent defects
have actually lived. **It is not a capability reading and cannot be made into
one.** The rates it prints describe the mock's answers, so the run records
`provider: mock`, stays out of `benchmarks/results/`, and is refused by
`avs bench-criterion` if it reaches that directory anyway (ADR-056). Whether
the *system* is capable — the build and probe floors the kill criterion reads
(`BUILD_FLOOR` and `PROBE_FLOOR` in `bench_criterion.py`) — is only ever
measured against a real provider.
