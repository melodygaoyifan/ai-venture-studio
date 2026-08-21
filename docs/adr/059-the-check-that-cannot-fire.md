# ADR-059 — a check that cannot fire, and the plan that learned to keep it quiet

**Status:** accepted (2026-08-20)

**Answers:** the one finding ADR-058 named and deliberately left open —
`lane_check` skips same-lane pairs, so a single-lane plan cannot collide no
matter what its tasks touch.

**Reverses:** nothing. ADR-058 recorded this as out of scope and said so in
`HISTORY.md` rather than widening that release. This is the deferred decision
being made, not a reversal of it.

## Context

`lane_check` compares tasks in **different** lanes. Same-lane overlap is
skipped, and the reason given is true: `schedule_waves` admits at most one
task per lane per wave, so same-lane tasks never build concurrently. Nothing
about that guarantee is inert — it was verified, not assumed.

The consequence is the problem. A plan with one lane cannot collide, so:

- the check that exists to protect parallelism is silent exactly when there is
  no parallelism left to protect, and
- a planner can clear a genuine collision by merging the two lanes, and be
  answered with silence.

Run 18 scored both sides of that in a single run:

| case | lanes | outcome |
|---|---|---|
| 01-groupbuy-api | `data` holding the shared model, `api` beside it | passed, correctly |
| 02-shortener-api | collapsed to one | passed, unexamined |
| 04-direction-workbench | collapsed to one; three tasks all expect `app/candidates.py` | passed, unexamined |
| 03-groupbuy-auto | two honest lanes over one shared model | blocked at Gate U2, **built nothing** |

Two of the three passing plans passed by removing the parallelism the check
exists to protect. The plan that kept two honest lanes is the one that died,
and it cost the run its build rate.

ADR-058 gave that dying plan three named remedies, one of which is MERGE. So
the previous record made the dodge **easier to find** — correctly, since MERGE
is often the right answer, but with no word about what it costs.

## Decision

### The finding is described, recorded and delivered — and never blocks

`run_planning` computes `status="blocked"` from `dag_issues` alone. Anything
added to `lane_check` therefore blocks the plan, and a planner handed a bar it
cannot clear burns `MAX_REVISIONS` and builds nothing. That is not a
hypothetical failure mode; it is what happened to case 03, and it is the whole
subject of the record this one continues.

There is also no honest deterministic rule separating "these three tasks are
one surface" from "the planner gave up". A single-lane plan is legal and is
frequently right.

So `lane_advisories` returns observations, not issues:

- **collapse** — three or more tasks in one lane: names the wave count, states
  plainly that `lane collision` *cannot be reported for this plan at all*, says
  the arrangement may well be right, and gives the hoist if it is not.
- **same-lane contention** — tasks in one lane expecting the same glob: names
  the owners and the file, says it is safe and *why* the checker is quiet, and
  offers the same hoist.

Both are phrased to permit the status quo. An advisory that reads as an
accusation gets "fixed" by merging more lanes, which is the move it exists to
discourage.

### Three readers, chosen so none of them can force a revision

1. `plan.critic_issues`, severity `minor`, lens `parallelism` — recorded in
   `plan.yaml`. Minors do not trigger revision; majors do.
2. `plan.md` prints the lane **count**. The task table has always shown each
   task's lane and never how many lanes there are, which is the one number
   that says whether this plan builds concurrently or one task at a time.
3. The revision feedback, under `advisories_not_blocking`, **only when a
   revision is happening anyway**. The planner hears it for free while it is
   already rewriting, and never revises because of it. The clean-plan `break`
   comes first, and a test asserts that ordering.

### The MERGE remedy admits its price

`lane_check`'s MERGE option now says, in the same sentence, that the two tasks
will build one after the other and that collapsing every task into one lane
silences the check and builds the plan serially. It stays on the list — it is
often correct — but it is no longer the free-looking option.

### The bench row keeps the arrangement after the workspace is gone

`CaseResult.lanes` records `"1 lane(s) over 3 task(s): core"` plus the advisory
count, read from the workspace's own `plan.yaml`. Establishing that run 18's
cases 02 and 04 had collapsed required opening preserved workspaces by hand —
and run 18 had already overwritten run 17's (ADR-058, finding 1). The
arrangement is a deterministic fact about a file the bench already has; the row
is where it survives the workspace.

## What stays out

- **No new refusal, at any threshold.** Not "at least two lanes for plans over
  N tasks", not a warning that escalates after K runs. Every version of that is
  a bar the planner might not clear, and the cost of not clearing it is the
  entire product (case 03).
- **No feeding these to the critique roster.** `ParallelizationSafety` is
  already on the charter roster and can see the lanes itself. Handing it a
  deterministic finding creates revision pressure this record cannot bound —
  the roster decides severity, and a `major` costs a revision.
- **No auto-hoist.** The checker does not rewrite the plan. A checker that
  picks the arrangement is a planner (ADR-058's line, unchanged).
- **No re-scoring of run 18.** Case 04 passed and still passes. What changes is
  that run 19's row will say how.

## What keeps this honest

- `test_the_collapsed_plan_is_invisible_to_lane_check` asserts the **premise**
  rather than assuming it — if `lane_check` ever starts catching this, the
  advisory is redundant and that test says so first.
- `test_merging_the_lanes_clears_the_refusal` demonstrates the dodge on run
  18's actual case-03 arrangement. A planner optimising for a clean check finds
  this move on its own; the test proves it is there.
- `test_an_advisory_is_not_a_dag_issue` reads the source of `run_planning` and
  fails if `lane_advisories` is ever spliced into the `dag_issues` expression.
  That one line is the difference between this record and a repeat of case 03.
- `test_advisories_ride_along_only_when_a_revision_happens_anyway` asserts the
  `break` precedes the payload, so a clean plan cannot be sent back over an
  advisory.
- `test_the_remedy_the_advisory_recommends_actually_clears_it` runs the hoisted
  arrangement back through **both** `lane_advisories` and `lane_check` —
  advice that trades one finding for another is worse than silence (ADR-058).
- The behaviour was confirmed absent in the shipped 0.106.0 build before it was
  written, so the blind spot is a property of the released system rather than
  an artifact of this change's own refactor.

## The lesson worth keeping

ADR-048 named the instrument that cannot fire and reports as though it fired
and found nothing. This is the next turn of it: an instrument that cannot fire
in one arrangement, and a writer that can choose the arrangement.

When a check has a blind spot, the thing under test eventually finds it —
without meaning to, just by optimising for a clean result. So the question to
ask of a passing check is not "did it look?" but **"could it have failed
here?"** If the answer is no, a pass is not evidence, and the reader has to be
told which of the two it is holding.
