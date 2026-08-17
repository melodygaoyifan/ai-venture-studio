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
| 4 | result-2026-07-26-0259-run4-reconstructed.yaml (full) | 991c28c⁻ | 8% | 0% | 0% | infra noise: 2 cases crashed (KeyError new_content; brief YAML budget) |
| 5 | result-2026-07-26-0524-run5-reconstructed.yaml (full) | pre-8b422a3 | 33% | 0% | 17% | case 04 built 6/6, all probes dead: no boot contract + spec drift |
| 6 | result-2026-07-26-0706 (lost) | 8b422a3 | 72.4% | 23.3% | 41.2% | boot gate + scope law proven; remaining failures all contract-drift shaped |
| 7 | result-2026-07-26-0844-run7-reconstructed.yaml (partial) | 2f323e9 | 42% | 90% | 0% | both contract fixes land; whole-surface-in-one-task appears |
| 8 | result-2026-07-26-2152 (lost) | 2bb4808 | 44% | 75% | 0% | flat; case 01 cross-iteration phantom-import signature |
| 9 | result-2026-07-26-2320.yaml (original) | 043176b | 54% | 75% | 8% | case 01 perfect + first clean review; case 03 probegen produced 0 probes |
| 10 | result-2026-07-27-0129.yaml | a0a4469 | 75% | 75% | 43% | best composite; fixture visibility ended case-04 decomposition deaths; SSRF scoping moved clean reviews off 0% |
| 11 | result-2026-07-27-0449.yaml | f246a4c | 74% | 75% | 38% | scoreboard HOLDS (same code as run 10, repeat within noise); case 02 perfect for the first time — intensive run phase ended per the stopping rule |
| 12 | result-2026-08-13-0347.yaml | ddad6f2 | 75% | 65% | 48% | **first run ever driven by the scheduler** (v0.82.0 bench cadence loop, 16 days after the series stopped). Cases 01/02/03 ALL completed, 11/11 tasks built — the first three-case sweep; clean reviews 48%, the best recorded. Case 03 is **measured again after five runs unmeasured** (the 4a77fd3 probegen fallback works: 5 probes generated, 3 passed) and both its failures read `no error field: {}` — **which was the harness, not the product** (see the correction below). Case 04 did not run: `pytest -q` timed out at 300s and the case errored, scoring **0/0 → counted as 0.0** in all three aggregates, so the headline understates the three cases that ran (11/11 built = 100%) and the probe drop 75%→65% is that zero, not a capability regression |
| 13 | result-2026-08-13-0837.yaml | c3aa2e3 | **94%** | **92%** | **75%** | **best composite by a wide margin, and the first run where all four cases were measured** (`cases_measured: 4` of 4). The two v0.83.0 fixes were judged in anger: case 03 went **5/5 on probes**, up from 3/5 — both of its old failures were the `{}` error-body pair, now readable; case 04 **completed 6/6 with no timeout at all**, so `_run_and_classify` never fired and run 12's hang remains unexplained rather than fixed. Case 02 is the one real failure: the build gate never went green on *database-backed short-link store with atomic CLI* (first attempt an `ImportError` loading conftest, auto-retry `test_create_returns_201_with_code`), nothing committed, workspace reset — the system refusing to ship broken work. Case 04's single probe failure was `URLError: Connection refused` — **the harness again, fixed below** |
| 14 | result-2026-08-14-0139.yaml | f05f69d (v0.86.0) | **100%** | **100%** | 38% | **every task in every case built, and every probe passed** — 17/17 and 13/13, four of four cases measured. The composite's ceiling moved to the review: 37.5% clean, *down* from run 13's 75% on a run that built strictly more. That gap is what ADR-037 was written from — the repair pass filtered by a hard-coded severity list while the leader blocked on its own wider one, so MEDIUM could block a verdict that nothing would ever try to fix. Case 03 (0 of 3) and case 04 (0 of 6) took no clean review at all despite building everything. The file carries **no `avs_version`** — the run predates ADR-037's rule that a result names the build that produced it, which is why "which code scored this" had to be recovered from commit times |
| 16 | result-2026-08-16-0612.yaml | 08d8ba9 (v0.93.0) | **100%** | 75% | 31% | 3 of 4 cases measured — `02-shortener-api` is **excluded**, and both halves of that exclusion are wrong in opposite directions (see the note below). Every task in every case that ran was built: **16/16**, and **16/16 probes passed**. The ceiling is the review again: 5 clean of 16 tasks. **ADR-039 did what it was built to do and it did not move this number** — reviews now arrive at one or two findings each rather than one issue wearing nine hats, and a repair pass fires on all of them, but **6 of the 11 rejections read "a fix was attempted and rolled back — it did not clear the review"**. Two rejections are the reviewer earning its keep: `04-t1` held a shared `sqlite3` connection across `ThreadingHTTPServer` worker threads without `check_same_thread=False` (3 critical), and `04-t3` removed the non-integer id guard, answering 404 where it owed 400 — `ESCALATE_SECURITY_RISK`. Three more were not about severity at all (see below) |
| 15 | result-2026-08-14-0702.yaml | 9807c32 (v0.88.0) | 83% | **100%** | 55% | 3 of 4 cases measured — **04-direction-workbench died on a provider `529 Overloaded` and is excluded, not scored `0.0`** (ADR-035). Probes stayed perfect on everything that shipped. Two independent cases were blocked by a spec containing *nothing* — no criteria, no skeletons — reported as the flat sentence "no acceptance criteria"; an empty spec passed every quality check by having nothing to check, so the revision loop's good-enough break fired on it (ADR-041). Case 01 also lost a task to a build that failed `1 failed, 27 passed` three times, whose recorded cause was 240 characters of pytest banner art naming no assertion (ADR-042); re-running the preserved workspace showed a genuine product defect — a 40-digit id matching the spec's own `^[0-9]+$` criterion answered 400 where it owed 404 |

> **Comparability break after run 15 — read run 16's clean rate against
> nothing above it.** Four ADRs landed between run 15 and the next run, and
> three of them change what the numbers *mean* rather than how well the
> system works:
>
> - **ADR-039 (in v0.89.0) changes how findings are counted.** Run 15 ran on
>   0.88.0, so its 55% still contains location-keyed dedupe: one bandit hit
>   appearing in nine test files counted as nine blocking findings against an
>   eight-finding repair cap — a review that could not be cleared by
>   construction. Any movement in run 16's clean rate is that change first
>   and product quality second.
> - **ADR-041 (v0.91.0) changes what a blocked spec does.** The two empty
>   specs above would now be sent back with emptiness as the complaint
>   rather than accepted and reported as a judgment. Run 16's build rate
>   carries that.
> - **ADR-042 (v0.92.0) changes only what a failure *reads* like.** No
>   number moves for it; it is why the next one will be explainable without
>   re-running a preserved workspace.
>
> ADR-040 (v0.90.0) changes neither — it is the alert path, and run 15 was
> the first result it ever announced.

> **Run 16, corrected reading — the exclusion is per-rate, and it should be
> per-case.** The numbers above stand as recorded. But `02-shortener-api` is
> named in `unmeasured`, the run's own closing line says these "are excluded
> from the rates above, not scored as zero", and **that sentence is true of
> two rates out of three**. `unmeasured` is derived from `build_rate is None`
> (product_bench.py:472) while each rate is averaged independently
> (`_avg`, :461–471) — so the case was dropped from build and clean, and its
> probes were kept: two probes ran against a product that was never built,
> failed `AssertionError: no runnable entry point`, and entered the average
> as a real `0.0`. A case that is unmeasured is unmeasured in *every* rate;
> this is **run 12's error, in the same file, after the ADR written to fix
> it**, because ADR-035 asked "did this case produce a denominator?" and a
> case's probe list produces one whether or not there is anything to run it
> against.
>
> **But the fix is not to drop the probes — it is to stop excluding the
> case.** Case 02 was not a crash: it planned for 6.4 minutes and
> `run_planning` came back `blocked`, which `autopilot.py:302` turns into
> `status="failed"` **carrying no reason at all** — the same shape as
> ADR-041's empty spec and ADR-042's banner art, one stage further up. Zero
> tasks then read as "never ran", so a case where the machine produced no
> product at all was excluded from the build rate rather than scoring the
> `0.0` ADR-035 explicitly reserves for exactly that ("a case that ran and
> built nothing still scores a real 0.0"). Its two probes failing were
> *correct*: there was no product, and that is a fact about the run.
>
> **The honest reading of run 16 is `build 75% · probes 75% · clean 31%`
> over 4 of 4 cases** — 100% was the rate over the cases that got a plan,
> not over the cases that were asked for a product. Under ADR-043 the run
> would have reported those numbers with case 02 named as a 0.0 that says
> why, and `unmeasured` empty.

> **Run 16, the three rejections that were not about the code.** `01-t4`
> (`1 low — B310: blacklist`), `03-t3` (`1 low`) and `04-t6` (`2 low`) were
> all `REQUEST_CHANGES` on findings that **cannot block on severity** —
> `leader.py:125` blocks on `ACTIONABLE_SEVERITIES` (critical/high/medium)
> or on `len(blocked) == 2`, and `blocked` is voters whose status is not OK,
> i.e. reviewers that never returned a verdict. All three rows carry no
> `blocking_by_voter` at all, which is the tell. So three of eleven
> rejections — and three tasks that would otherwise have been clean — were
> caused by two reviewers failing to run, while the row's own recorded reason
> names low-severity findings that had nothing to do with it. **ADR-037's
> detail line exists so a rejection has a readable cause, and here it reports
> a cause that is not the cause.** Run 16 is the first run able to show this:
> the detail line only started populating in v0.87.0.

> **Run 16, the six rejections that were the repair pass, not the product
> (ADR-044).** The other six of the eleven read *"a fix was attempted and
> rolled back — it did not clear the review"*. `_fix_iteration` could fail in
> six ways and printed that one sentence for all of them; four of the six
> never reach a review at all. The dominant one is reproducible in three
> files: run 16's blocking findings are mostly test-duplication complaints
> (`01-t2`, `04-t6`, `04-t7` — *"instead of a shared fixture/helper"*), whose
> only repair is to hoist boilerplate into a helper, and `assertion_delta`
> judged one file at a time — so every assert moving into the helper read as
> `removed_assert` and `_write_files` dropped exactly the files carrying the
> fix while keeping the new helper. `written` was non-empty so nothing
> noticed; the untouched originals still passed the suite gate; the half was
> committed. The re-review then saw the duplication still present **plus an
> orphan helper nothing called** — which is `03-t5`'s recorded finding,
> *"Unused alias function diverges from spec's stated call path"*. The repair
> pass was manufacturing the findings that rejected it.
>
> Two more of the six are not repair failures at all: `04-t3`'s
> ESCALATE_SECURITY_RISK for *"input validation removed for non-integer
> candidate IDs"* describes the **discarded** diff — the rollback restored
> the guard, and it was in the delivered code the whole time the row said it
> was gone.
>
> None of this could be read from a preserved workspace, because there were
> none: all six tasks sat in cases that completed with every probe passing,
> and preservation fired only on a failed case or a failed probe. The clean
> rate is the number this bench most often has to explain and it was the only
> one whose evidence was deleted by design. Checked and refuted along the
> way: the repair pass was **not** truncating — the preserved run-14/15
> ledgers top out near 8k output tokens against a 16384 cap.
>
> **Run 17 is the first run in which the reviewer's most common request is
> performable.** Its clean rate is not comparable with anything above it.

> **Run 12, recomputed reading (ADR-035).** The numbers above stand as
> recorded — this table is a record, not a document — but two of them were
> read wrong on the day, and both readings are corrected here rather than
> overwritten above.
>
> - **Probes 65% was 87%.** Case 04 contributed no probes at all; averaging
>   its absence in as `0.0` cost 22 points. Over the three cases actually
>   measured the run scored **build 100% · probes 87% · clean 48%**.
> - **Case 03's two `no error field: {}` failures were the probe frame's,
>   not the product's.** Booted by hand the product answers `400 {"error":
>   "id must be a base-10 integer: 'abc'"}`. `urllib` raises `HTTPError` on
>   every 4xx and `call()` returned `e.code, {}` without reading the body.
>   Run 7 showed the identical failures and the response then (`2bb4808`)
>   was to tighten the *product* prompt — a fix one layer too low, which
>   could never have worked while the probe could not see the field.

> **Run 13, the one probe failure that was not the product's.** Case 04's
> `score-validation-and-evidence-downgrade` failed with `URLError:
> Connection refused`, which means the probe never reached the product —
> nothing was measured about its behaviour, and it still cost the case a
> probe (3/3 → 2/3). The cause was the frame's own: every probe is a
> separate process that boots its own server, and the port was the
> **constant 8646**, so a probe could boot onto the port the previous
> probe's server had not finished releasing. Readiness made it worse by
> being a bare TCP connect, which succeeds against a socket that is already
> closing — the check said "up" about a server that was on its way out.
> Fixed in v0.84.0: an ephemeral port per probe, readiness that requires an
> HTTP answer (404 counts, a transport error does not), one retry before
> believing a refusal, and `proc.wait()` after `terminate()` so the port is
> actually back before the probe process exits. This is the same shape as
> run 12's `{}` failures and run 7's before it: **the harness charging the
> product for the harness's own miss.** The metric's *definition* does not
> change, so the baseline is not reset; but runs ≥ 14 no longer carry this
> source of false probe failures, and a probe rate that ticks up across
> that line has one more reason to do so than product quality.

> **Comparability break after run 12:** ADR-035 changes the denominators.
> A case that produced no denominator is dropped from the rate rather than
> averaged in as `0.0`, and the saved result carries `cases_measured` /
> `cases_total` / `unmeasured` so a later reader can tell 75%-of-four from
> 75%-of-three. A case that ran and built nothing still scores a real
> `0.0`. Runs 1–11 contain no crashed cases except run 4 (already recorded
> as noise), so the floors' basis is unaffected; runs ≥ 13 use the new
> denominators.

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
