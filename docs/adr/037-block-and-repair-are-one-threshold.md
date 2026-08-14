# ADR-037 — a severity you block on is a severity you must try to repair

**Status:** accepted (2026-08-14)

**Answers:** why bench run 14 scored clean-review 38% — the worst in the
series — while its build and probe rates were the best ever recorded
(100%/100%, run 13 was 94%/92%).

**Reverses:** nothing. ADR-035's rates and denominators stand; ADR-036's hang
instrumentation stands and was neither confirmed nor contradicted by run 14
(nothing hung).

## Context

Run 14's rows looked contradictory: every case completed, every task built,
every behavioral probe passed, and two of the four cases scored **zero** clean
reviews across nine tasks. Each of those rejections carried an empty reason.

The reviewer had not become stricter. Two thresholds that must be the same
number were written in two places and had drifted:

- `leader.synthesize` blocks a verdict on
  `_ACTIONABLE_SEVERITIES = {CRITICAL, HIGH, MEDIUM}`.
- `autopilot.review_and_repair` selected the findings to repair with its own
  hard-coded `f.severity.value in ("critical", "high")`.

So a task whose worst finding was **MEDIUM** was marked `REQUEST_CHANGES`,
**no repair was ever attempted on it**, and nothing later in the run could
clear it. Not unclean because the work was poor — unclean by construction.

This is not a corner case. Medium is the modal severity the voters raise: 89
of ~187 findings across run 13's preserved workspaces, against 17 high and 2
critical. The gap covered most of the unclean rows in both runs.

| | run 13 (v0.84.1) | run 14 (v0.85.0) |
|---|---|---|
| `REQUEST_CHANGES`, repair ran (crit/high) | 3 | 4 |
| `REQUEST_CHANGES`, no repair possible (medium-only) | 2 | **7** |

The repairable class was flat. The entire collapse sits in the class the
system blocked on and never tried to fix. Neither release changed
`leader.py` or any voter — `v0.84.1..v0.85.0` touched only `product_bench`,
`testing`, `build`, `probegen`, `screenshots`. **The defect was constant in
both runs; only the exposure varied.** 38% was not a regression to hunt, and
75% was never as solid as it read.

## The second failure, on the same rows

`detail` was written only when a fix iteration ran. A medium-only rejection
ran no fix, so it reached the scoreboard as `REQUEST_CHANGES` with an empty
reason — 11 of run 14's 17 outcomes. The run could report *that* it rejected
the work and not *what it objected to*.

That is ADR-036's evidence-deletion failure one stage over. A reviewer
rejecting everything correctly and one rejecting everything spuriously
produced byte-identical records, so the number could not be interpreted from
the artifact that reports it.

## Decision

1. **One threshold, imported, never re-listed.** `ACTIONABLE_SEVERITIES` is
   public in `leader.py`; `review_and_repair` filters by it. The repair pass
   can no longer disagree with the verdict about what counts.
2. **Every non-clean verdict records what made it non-clean.** A bounded
   `[review: 2 medium, 1 high — title; title]` note, capped at 240 chars,
   built from the review that produced the *final* verdict — after a fix
   iteration that is the re-review, so the row never names findings that
   were just repaired.
3. **A rejected row keeps its whole reason.** The 200-char clip now applies
   only to genuinely clean rows, where there is nothing to explain.
4. **A result file names the build that produced it** (`avs_version`).
   Attributing run 14 to a version required diffing git commit timestamps
   against the result's filename, and that only worked because the release
   landed 9 minutes before the run started.

## What keeps this honest

- `test_every_severity_that_blocks_a_verdict_is_one_the_repair_pass_attempts`
  is parametrized over `ACTIONABLE_SEVERITIES` itself: it asserts each
  severity really blocks a leader verdict **and** that a review containing
  only that severity triggers a repair attempt. Removing MEDIUM from repair
  now fails unless it is also removed from blocking. **No test pinned these
  two together before — that is why they drifted.**
- `test_the_reason_describes_the_review_that_produced_the_verdict` fails if
  the pre-fix findings are reported beside a post-fix verdict.
- `test_a_clean_verdict_does_not_carry_a_findings_dump` guards the other
  direction: `APPROVE_WITH_NOTES` must not turn a row into a transcript, and
  a low-only review must not spend a repair round-trip.
- `test_the_repair_set_is_not_re_listed_in_autopilot` strips comments before
  matching, because the comment above the fix loop quotes the old pair to
  explain the drift — the same trap as asserting `"PORT"` against a message
  headed `IMPORT GATE`.
- **Stated rather than left implied:** the repair pass is still bounded — one
  iteration, at most 8 findings, rolled back if the suite does not re-pass.
  A task carrying nine mediums can still end `REQUEST_CHANGES` with eight of
  them fixed. What is claimed is only that the system now *attempts* what it
  blocks on, and says what it found.
- **Not claimed:** that this raises clean-review rate. It should, since seven
  of run 14's eleven rejections now get a repair attempt they never had. But
  the number that matters is the one run 15 measures, and the fix could as
  easily surface fix-iteration rollbacks that were previously invisible.
