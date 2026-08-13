# Product-bench history

The scoreboard across the real-product bench runs. On 2026-07-26 the
canonical gitignored `.mas/product-bench/` was deleted mid-session and runs
1–8's original result files were lost; this directory is the durable,
git-tracked record (and `save_summary` now writes every new result here
automatically). Files marked *reconstructed* were rebuilt from complete or
partial copies held in the working session; the table below is authoritative
for the headline numbers.

| Run | Result file | Code | Build | Probes | Clean | Notes |
|----|----|----|----|----|----|----|
| 1 | result-2026-07-23-0507 (lost) | pre-fixes | — | — | — | numbers not preserved |
| 2 | result-2026-07-24-0507 (lost) | pre-fixes | 14% | 0% | 50% | 2 cases only; write-lock instant-fatal era |
| 3 | result-2026-07-24-0700 (lost) | pre-2ac1bd4 | 18.5% | 0% | 11% | skeleton-reword deaths dominated |
| 4 | …0259, reconstructed (full) | 991c28c⁻ | 8% | 0% | 0% | infra noise: 2 cases crashed (KeyError new_content; brief YAML budget) |
| 5 | …0524, reconstructed (full) | pre-8b422a3 | 33% | 0% | 17% | case 04 built 6/6, all probes dead: no boot contract + spec drift |
| 6 | result-2026-07-26-0706 (lost) | 8b422a3 | 72.4% | 23.3% | 41.2% | boot gate + scope law proven; remaining failures all contract-drift shaped |
| 7 | …0844, reconstructed (partial) | 2f323e9 | 42% | 90% | 0% | both contract fixes land; whole-surface-in-one-task appears |
| 8 | result-2026-07-26-2152 (lost) | 2bb4808 | 44% | 75% | 0% | flat; case 01 cross-iteration phantom-import signature |
| 9 | result-2026-07-26-2320.yaml (original) | 043176b | 54% | 75% | 8% | case 01 perfect + first clean review; case 03 probegen produced 0 probes |
| 10 | result-2026-07-27-0129.yaml | a0a4469 | 75% | 75% | 43% | best composite; fixture visibility ended case-04 decomposition deaths; SSRF scoping moved clean reviews off 0% |
| 11 | result-2026-07-27-0449.yaml | f246a4c | 74% | 75% | 38% | scoreboard HOLDS (same code as run 10, repeat within noise); case 02 perfect for the first time — intensive run phase ended per the stopping rule |
| 12 | result-2026-08-13-0347.yaml | ddad6f2 | 75% | 65% | 48% | **first run ever driven by the scheduler** (v0.82.0 bench cadence loop, 16 days after the series stopped). Cases 01/02/03 ALL completed, 11/11 tasks built — the first three-case sweep; clean reviews 48%, the best recorded. Case 03 is **measured again after five runs unmeasured** (the 4a77fd3 probegen fallback works: 5 probes generated, 3 passed) and both its failures are the same known shape, `no error field: {}` — the empty-4xx-body defect 2bb4808 and dd81a41 targeted has **not** held here. Case 04 did not run: `pytest -q` timed out at 300s and the case errored, scoring **0/0 → counted as 0.0** in all three aggregates, so the headline understates the three cases that ran (11/11 built = 100%) and the probe drop 75%→65% is that zero, not a capability regression |

> **Comparability break after run 11:** v0.65.0 added the in-run auto-retry
> (each failed task re-attempted once with its failure as context) and later
> commits added cross-run failure memory. Runs ≥ 12 therefore measure the
> pipeline *with* self-retry — build rates are expected to move up for
> reasons unrelated to writer quality. Compare runs ≥ 12 against each other,
> not against this table's trajectory.

Fix lineage: 991c28c (bench crash fixes) → 8b422a3 (scope law, boot gate,
workspace hygiene) → 972a680 (spec gets the FDR verbatim) → 9ea8769 (bench
single-instance lock) → 2f323e9 (implementer gets the FDR; no-crash rule) →
2bb4808 (additive-only fixtures, tests-only close, error bodies) → 3b327ab
(private names exempt; stale-import feedback) → this commit (fixture
visibility in the implementer prompt, probegen retry + unmeasured-case
visibility, SSRF probe scoped to non-test code, results dual-written here).
