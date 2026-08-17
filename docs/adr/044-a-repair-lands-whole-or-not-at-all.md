# ADR-044 — A repair lands whole, or lands nothing and says why

Status: accepted (v0.95.0)

## Context

Bench run 16's clean-review rate fell to 31% from run 15's 55%. Six of its
eleven rejections carried the same sentence:

> a fix was attempted and rolled back — it did not clear the review

The sentence was usually false, and it was the only thing the durable record
said. `_fix_iteration` could fail in six different ways and every one of them
printed that one line — the line that names a review, when four of the six
never reached one. So "the reviewer got stricter" and "the repair pass stopped
working" were byte-identical in the record. That is ADR-036's shape, and it is
why the fall could not be read off the scoreboard at all.

It was the second one. The repair pass had stopped working, in a specific and
reproducible way.

**The reviewer's most common request was structurally impossible to perform.**
Run 16's blocking findings are dominated by duplication complaints against
test files — *"Duplicated inline HTTP-test harness (FakeRfile/TestHandler)
instead of a shared helper"* (01-t2), *"Near-identical test boilerplate
duplicated across four new test files"* (04-t6), *"Test boilerplate duplicated
verbatim across six new test files instead of a shared fixture/helper — the
exact anti-pattern a prior task in this repo was already dinged for"* (04-t7).
The repair for every one of them is the same: hoist the boilerplate into a
shared helper and call it from the sites.

`assertion_delta` judged one file at a time. An assert moved out of a call
site and into the helper read as `removed_assert`, so `_write_files` dropped
every rewritten call site — and kept the new helper, which had nothing wrong
with it. The reproduction is three files long:

```
WRITTEN: ['tests/helpers.py']
KEPT (dropped):
    tests/test_api1.py (skeleton kept — your version dropped: removed_assert: ...)
    tests/test_api2.py (skeleton kept — your version dropped: removed_assert: ...)
```

**And the half was committed.** `written` was non-empty, so nothing detected
the partial application. The untouched originals still asserted, so the suite
gate passed. The commit went in. The re-review then saw the duplication still
present *plus an orphan helper nothing called* — and flagged it: *"Unused
alias function diverges from spec's stated call path"* (03-t5). The repair
pass was manufacturing the findings that rejected it, and the row charged them
to the product.

`_write_files` returns exactly the fact that explains this, and the call site
discarded it: `written, _kept = _write_files(...)`.

**A rolled-back repair still set the verdict.** When `_should_roll_back` fires,
`git reset --hard` restores the pre-fix tree — and the caller kept the post-fix
review anyway. Run 16's `04-t3` is recorded `ESCALATE_SECURITY_RISK` for
*"Input validation removed for non-integer candidate IDs"*. The repair removed
that guard; the rollback put it back. The guard was in the delivered code the
entire time the scoreboard said it was gone. The row described a diff that no
longer existed and omitted the findings that described what shipped.

**The evidence was deleted by design.** All six rolled-back tasks lived in
cases that completed with every probe passing, and `run_case` preserved a
workspace only when the case failed or a probe failed. Not one of those
reviews survived. Diagnosing this had to proceed from truncated `detail`
strings in the result YAML, and the one run whose ledger records `stop_reason`
— the run that could have proved or refuted the truncation hypothesis — had
deleted itself. (It refuted it: the preserved ledgers from runs 14 and 15 top
out around 8k output tokens against a 16384 cap, so nothing was being cut off.
That hypothesis was wrong and is recorded here because it was checked.)

## Decision

**The write-guard judges the batch, not the file.** `assertion_delta` takes
`elsewhere=` — the other files written in the same response. An assert that
leaves one file and lands unchanged in another has moved, and a move is not a
weakening: the suite still asserts it. The reward-hacking defence is untouched
— an assert that appears nowhere in the batch is still a removal, and
`added_skip` is never forgiven by relocation, because there is nowhere for a
skip to have moved from.

**A partly-refused repair is applied to none.** If `_write_files` drops any
file of a repair batch, `_fix_iteration` restores the tree and reports which
files were refused. A repair is a set of coordinated edits; applying the
subset that passed the guard is how an orphan helper gets committed and then
blamed on the product.

**Every failure path names itself.** `_fix_iteration` returns
`(landed, after, why)`. An AST walk over the function rejects any
`return False, ...` without a reason, so a seventh path cannot be added
silently. The repair pass also now checks `last_response_truncated()` — every
other writer stage has since ADR-041, and this was the one stage that never
did while being the stage that must return complete file bodies.

**The verdict describes the code that survived.** `after` sets the verdict
only when the repair landed. A discarded attempt is evidence about the repair
pass, so it rides in the reason, not in the verdict.

**A rejection is preserved like a crash.** `run_case` preserves a workspace
when `len(clean) < len(built)`, so the number this bench most often has to
explain stops being the one whose evidence is thrown away.

## What this reverses

Nothing in ADR-037 or ADR-038. ADR-038's `ROLLBACK_SEVERITIES` stays narrower
than `ACTIONABLE_SEVERITIES` and is untouched; what changes is that a rollback
no longer carries its review into the row. ADR-037's rule that a landed fix's
re-review is the recorded verdict stands and is pinned by its own test here.

The §13.29.5 write-lock is narrowed for the first time since it was written,
and deliberately only along the axis that made it self-defeating. It exists so
an implementer cannot delete an assert to make its build pass. Moving an
assert into a helper in the same batch does not make anything pass that was
failing.

## What stays out

- **Letting the repair pass edit tests freely.** The guard catches real
  reward-hacking and the suite gate does not replace it: a repair that deletes
  asserts leaves a *greener* suite, not a redder one.
- **Demoting duplication findings to LOW.** The reviewer is not wrong that six
  copies of a fixture is a defect — it was right and the machine could not act
  on it. Silencing the reviewer to raise the clean rate is scoring the metric,
  not the product.
- **Widening `ROLLBACK_SEVERITIES` to medium.** ADR-038's reasoning is intact:
  rolling back on medium discards nearly every fix and returns the repair pass
  to a no-op that looks like it ran.
- **Rewriting run 16's recorded numbers.** They are what the run measured.
  Run 17 is the first run in which the reviewer's most common request is
  performable.

## Mechanism

`assertion_delta(..., elsewhere=)` in `tools/integrity.py`, fed the batch by
`_write_files` in `upstream/build.py`; the `kept` refusal, the truncation
check, the `why` return and the verdict/rollback split in `_fix_iteration` and
`review_and_repair` in `upstream/autopilot.py`; the preservation condition in
`run_case` in `product_bench.py`.

`tests/test_a_repair_lands_whole_or_not_at_all.py` — eleven tests pinning the
rule rather than run 16's instance: the move/deletion distinction in both
directions, the consolidation refactor driven end to end through the real
writer, the half-application leaving no commit and no orphan, the AST walk
over every `return False`, and the verdict following the tree in both
directions.
