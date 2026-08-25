# ADR-080: a nudge is not a revision

Date: 2026-08-25
Status: accepted
Release: v0.122.0

## Context

Run 19b, case 04, third run — the first with ADR-079's evidence
preservation live, which is why this one could be debugged by reading.
`.mas/failed-plans/` holds exactly one file, `attempt-3.txt`: attempts 1–2
parsed and were revised on substance (critic/dag feedback), attempt 3 broke
at YAML line 4, column 187 — an unquoted description containing
`{"error": …}`, whose `": "` makes YAML read "mapping values are not
allowed here". The blocked reason now names that exact break.

The structural defect: parse failures and substantive revisions shared one
budget, `MAX_REVISIONS`. A parse failure landing on the last revision —
which is where it landed — exhausts the loop, so the corrective feedback
(since ADR-079, finally naming the line and column) is composed and never
shown to the model. The case died with the cure in hand. ADR-075 met the
identical shape at the voters (protocol failures consuming verdicts) and
answered with bounded nudges; the planner never got them.

## Decision

`_MAX_PARSE_NUDGES = 2`. A planner response that fails to parse spends a
nudge, not a revision; only when the nudge budget is exhausted do further
parse failures consume revisions. The loop terminates after at most
`MAX_REVISIONS + 1 + _MAX_PARSE_NUDGES` provider calls. Preserved-attempt
files are named by a total attempt counter (`attempt-N.txt`), so nudged and
revised attempts share one series. `plan.revisions` continues to count
substantive re-asks only — a run whose only failures were protocol
failures records `revisions: 0`.

## Consequences

- The revision loop's guarantee is now: a parse failure is ALWAYS answered
  at least once with feedback that names the break. Under the old budget
  that guarantee held only if the failure arrived early.
- Worst case planner spend rises from 3 to 5 calls, only when responses
  repeatedly fail to parse — the case that was previously a guaranteed
  dead product, i.e. the extra calls replace a full case loss.
- `tests/test_a_nudge_is_not_a_revision.py` pins both sides: two parse
  failures then a good plan → `revisions == 0`, three planner calls, two
  preserved attempts; a never-parsing planner → blocked after exactly
  `MAX_REVISIONS + 1 + _MAX_PARSE_NUDGES` calls.
- ADR-079 + ADR-080 are one lesson in two layers: first make the failure
  legible (name the break, keep the evidence), then make the loop able to
  act on it (budget the retry so the feedback is actually deliverable).
