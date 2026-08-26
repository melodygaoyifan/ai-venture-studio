# ADR-081: the repair hears why it was discarded

Date: 2026-08-26
Status: accepted
Release: v0.123.0

## Context

Run 19b, case 04, final run (v0.122.0): task t8's review found a critical
— "New 405 guard makes POST /api/candidates unreachable". The repair pass
was attempted, broke the suite, and was correctly discarded. That ended
the matter: the 405 guard shipped, and all three probes died on
`AssertionError: (405, {})` — the review had predicted the probe failures
exactly, and the machine still delivered the defect it had named.

The structural gap: a discarded repair was final. `_fix_iteration` can
fail six ways and names which one (ADR-044), but the reason was composed
for the scoreboard and shown to no model. This is ADR-080's shape one
gate later: the corrective feedback existed at the moment of failure and
nothing was allowed to act on it. Three of the run's eight reviewed tasks
carried "repair attempted, not applied" rows.

## Decision

`MAX_REPAIR_ATTEMPTS = 2`. A discarded repair buys one more pass, and the
retry is *informed*: `_fix_iteration(prior_failure=…)` puts the discard
reason in a `<previous_repair_attempt>` block of the implementer prompt.
When the discard came from the re-review (rollback), the discarded diff's
own blocking findings ride in that block too — the retry hears *what* its
predecessor broke, not just that it broke something.

The row's rules are unchanged: the verdict describes the code that
survived (ADR-037/044), so the discarded diff's findings never reach the
row. A landing on the second attempt records the first discard as a
clause ("an earlier repair attempt was discarded: …"); a double failure
names each attempt's distinct reason ("…; then …").

## Consequences

- Worst case per unclean task rises from 1 implementer call + 1 re-review
  to 2 + 2 — spent only on the path that previously guaranteed the
  finding shipped unrepaired.
- The retry prompt is the first consumer of ADR-044's six named failure
  reasons beyond the scoreboard — the observability bought there now
  steers a decision.
- `tests/test_the_repair_hears_why_it_was_discarded.py` pins: the retry
  hears the reason; the discarded diff's findings reach the prompt and
  not the row; the budget is exactly `MAX_REPAIR_ATTEMPTS`; a landed
  first attempt buys no second; identical failures are narrated once.
- ADR-079 → ADR-080 → ADR-081 are one lesson at three gates: first make
  the failure legible, then make the loop able to deliver the feedback,
  then make every gate that composes a failure reason show it to the
  actor who can act on it.
