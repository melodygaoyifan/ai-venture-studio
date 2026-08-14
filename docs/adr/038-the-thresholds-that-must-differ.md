# ADR-038 — the thresholds that must differ, and the one word "clean"

**Status:** accepted (2026-08-13)

**Answers:** the pre-run audit asked of ADR-037 — *is everything of this shape
already fixed?* It was not. ADR-037 fixed one instance of "one concept, two
definitions" and left three more, plus a trap of its own making.

**Reverses:** nothing. ADR-037 stands and this completes it.

## Context

ADR-037's lesson was that two thresholds which must be the same number were
written in two places and drifted. Fixing the instance is not the same as
fixing the class. A sweep for the shape found:

1. `_fix_iteration` still rolls a fix back on its own hard-coded
   `("critical", "high")` — the very pair ADR-037 deleted one function above.
2. `product_bench` counted a clean review as `("APPROVE",
   "APPROVE_WITH_NOTES")` **as a literal**, beside the `CLEAN_VERDICTS`
   constant ADR-037 added to that same file and did not route it through.
3. `review_and_repair` re-listed the same pair a third time.
4. The founder-facing tally counted only `APPROVE` as clean.

## The one that must NOT be made to match

(1) reads like drift left behind, and changing it to `ACTIONABLE_SEVERITIES`
would have been the natural "consistency" fix. It would also have been a
serious regression.

Medium is the modal severity a review raises. A re-review of any real diff
almost always still contains one. Rolling back on medium would therefore
discard nearly every fix — turning the repair pass ADR-037 exists to enable
back into the no-op ADR-037 exists to remove, **while looking like it ran**.
Run 15 would have measured a fix pass that reverted itself.

So this is a codebase where the last ADR was about two thresholds that must
match, containing two thresholds that must differ, that look identical. The
answer is not to unify them but to **name the difference and pin it**:
`ROLLBACK_SEVERITIES` is now a named constant carrying why it is narrower,
and a test asserts it stays a strict subset.

## Decision

1. **`ROLLBACK_SEVERITIES`**, named and commented, with `_should_roll_back`
   as a pure seam so the threshold is testable without driving git and a
   model call.
2. **One definition of "clean."** `state.CLEAN_VERDICTS` lives beside the
   `Verdict` enum and is *derived from it*, so a new approval-shaped verdict
   cannot be added to the taxonomy and silently missed. All three call sites
   import it.
3. **The founder tally reports three states, not two.** A `REQUEST_CHANGES`
   task printed the same "built, review had notes" as an approval, so the
   founder could not distinguish work the reviewer signed off on from work it
   refused. Tolerable while those rejections were also reasonless; not once
   ADR-037 made them say what they objected to.
4. **`BENCH_TIMEOUT_S` 6h → 8h.** Run 14 used 11,206s (3.1h) of the 6h, which
   read as ample. ADR-037 sends every medium-only review into a fix iteration
   plus a re-review, so most of the ~17 tasks now spend two model round-trips
   they did not before. The margin was sized against runs that never did
   that. Too high costs nothing when the run finishes early; too low reports
   a capability failure that did not happen, on the only kill criterion the
   launch PRD has left.

## What stays out

`automation.MERGEABLE_VERDICTS` has the same two members and is deliberately
**not** unified with `CLEAN_VERDICTS`. It answers a different question —
whether a human-armed policy may merge the branch (ADR-031) — and equal
membership today is coincidence, not identity. Merging them would couple a
governance gate to a scoreboard definition. Same reasoning keeps
`review_gate._BLOCKING_SEVERITIES` (voter fixture registration) and
`deploy/review.py`'s deploy gate separate.

The rule this records: **unify definitions of one concept; name and pin
definitions of two.** Collapsing distinct thresholds because their members
match today is the same class of error as letting one threshold drift.

## What keeps this honest

- `test_a_fix_is_not_rolled_back_for_the_severity_that_prompted_it` asserts
  the strict-subset relation *and* the behaviour, so a future consistency
  sweep cannot quietly widen it.
- `test_clean_has_exactly_one_definition` checks the derivation from the enum,
  not just the current members.
- `test_the_clean_pair_is_not_re_listed` scans both modules' source with
  comments stripped — the comments quote the old literal to explain the
  drift, and matching them would be the `"PORT"`-in-`IMPORT GATE` trap a
  third time.
- `test_the_tally_does_not_read_a_rejection_as_an_approval`.

**Not claimed:** that any of this raises clean-review rate. (1) protects the
repair pass from being silently reverted, (2)–(3) are reporting integrity,
(4) is headroom. What run 15 measures is still what ADR-037 predicted, no
more.
