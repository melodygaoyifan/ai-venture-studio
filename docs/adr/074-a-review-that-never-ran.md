# ADR-074 — a review that never ran is not a review that objected

**Status:** accepted (2026-08-24) · **Release**: v0.116.0

## Context

The question asked was whether run 19 can be the last run — whether every
known defect from run 18 is fixed, so that the reading run 19 buys is worth
what it costs. Eleven were, and this is the twelfth, found in run 18's own
recorded output rather than by a new instrument.

`04-direction-workbench t4` reads:

```yaml
- task_id: t4
  title: Get single candidate with rounds
  status: built
  review: null
  detail: ''
  test_summary: 26 passed in 15.23s
```

Every other rejected row in that run explains itself — ADR-037 exists for
exactly that. This one is the only row in the run with an empty `detail`, and
it was scored against `clean_review_rate` as though a voter had objected.

Nothing objected. The preserved workspace still held the evidence:

```yaml
# .mas/product-bench/workspaces/04-direction-workbench/.mas/reviews/823496877eba/02-dor_fail.yaml
node: dor_fail
step: 2
reasons:
- diff too large (2361 lines > 2000); split the PR
```

The graph stopped at step 2. No voter ran. And the 2361 lines are worth
splitting apart, because they are not what the sentence implies:

| | lines | share |
|---|---|---|
| `product/*.yaml`, `product/*.md`, `specs/*/spec.*`, `FDR.md`, `SERVICES.md` | **1697** | **72%** |
| `app/` + `tests/` — the code | 664 | 28% |

The review was refused over the volume of the system's own generated
paperwork. The code under review was 664 lines, a third of the ceiling.

`git log` says why it lands in one commit: `d02df12`, the **first** feature
commit of the case, is `32 files changed, 2361 insertions` — the product
scaffold (four spec directories, the plan, the constitution, `conftest.py`,
eight test files) commits together with the first task's code. So the victim is
not random. **The bigger the case, the likelier its opening task goes
unreviewed**, and case 04 is the biggest case in the suite. This is a
systematic bias against the cases that exercise the system hardest, and it
would have recurred in run 19.

Two defects share the row.

**The reason was discarded one frame away.** `run_review` returns
`tuple[LeaderResult | None, ReviewState]`, and `_review_head` unpacked it
`review, _`. The state holds `dor_reasons`. So "the reviewer read the code and
would not sign it off" and "the reviewer was never allowed to look" arrived at
the caller as the same `None`. The reason existed twice — in memory and on disk
— and was written down neither time. ADR-042's shape exactly.

**The instrument's failure was charged to the product.** This is the failure
ADR-061 fixed for probes and left open for reviews, and `clean_review_rate`
already carried the correct rule in its own comment, one property away from the
one that got it wrong:

> a case that built nothing has no review to be clean, and the failure is
> already fully counted one column left

The same sentence one scope down: a *task* the reviewer never looked at has no
review to be clean either.

## Decision

**Unjudged is neither clean nor unclean, and the exclusion is named.**

1. `_review_head` returns `ReviewDidNotRun(reasons)` instead of `None` —
   **falsy on purpose**, so all four call sites (`if review`, `if after`,
   `after.findings if after else []`, `review.verdict.value if review else
   None`) and the dozen test stubs that return a review object keep behaving
   exactly as they did. A new return arity would have touched twelve unpacking
   sites for no gain; what changes is only that a caller may now *ask* why.
2. `review_and_repair` writes the gate's own words into the row:
   `[the review did not run: diff too large (2361 lines > 2000); split the PR]`.
3. `CaseResult.unreviewed` holds those task ids; `clean_review_rate` divides by
   `tasks_built - len(unreviewed)`, and is `None` — never `0.0` — when no task
   in the case was judged.
4. `BenchSummary.no_review_reading` names them as `case:task_id`, in the saved
   result file, in the CLI's denominator line, and in the Discord alert.

Keyed on the absent verdict, not on the detail string: a row's prose is for a
human and **a denominator must not depend on wording**.

### What run 18 would have read

Replayed through this build, from the recorded rows:

| | build | probes | clean |
|---|---|---|---|
| as recorded (v0.106.0) | 75% | 75% | 49.8% |
| under v0.116.0 | 75% | **100% over 3 of 4** | **52.2%** |

`no_probe_reading: ['03-groupbuy-auto']` · `no_review_reading:
['04-direction-workbench:t4']`. Two of the three headline numbers were
depressed by harness faults, and both now say so out loud. **Run 18 is not
re-scored** — the file records the version that produced it, as with ADR-058,
ADR-059 and ADR-061. What changes is what run 19 measures.

### The exclusion has a price and it is paid in public

ADR-061's trap, one level down: once unjudged tasks leave the denominator, a
run where the DoR gate refuses everything computes a clean rate over almost
nothing and looks fine. So the list is not optional and not a count — it names
which task, in every renderer that prints the rate. `no_review_reading` covers
**every** axis, not just `build`: an increment case builds a base product
through the same path, and while the clean rate averages build-axis cases only,
the list is a statement about what happened in the run.

## Consequences

The control was run in both directions. Reverting the denominator to
`/ tasks_built` fails three tests; discarding `dor_reasons` again fails a
fourth. Both were restored by hand rather than by `git checkout`, which reverts
the whole file (ADR-069's trap).

`tests/test_a_review_that_never_ran.py`, 16 tests. Suite 2501 → **2517**.
Version 0.115.0 → **0.116.0**.

## What stays out

**Raising `max_reviewable_lines`, or excluding generated paperwork from the
size count.** Both would make the review actually *run* on the scaffold commit,
which is better than excluding it — but voters receive the whole diff
unfiltered (`fetch_diff` does no path filtering), so counting fewer lines than
are sent would be the same defect inverted: a ceiling that no longer measures
what the reviewer is asked to read. The honest version is to keep planning
artifacts out of the review diff *entirely* — they have their own gate in U3
and `ears_lint` — and that changes what `avs review` reviews for every user of
the command, on a judgment about review semantics rather than a defect. It is
recorded here as the next decision, not taken as a side effect of this one.

**Refusing to build when the scaffold commit will exceed the ceiling.** A new
bar the planner cannot clear is what killed case 03 in run 18 (ADR-058), and
the lesson there was explicit: describe, do not refuse.
