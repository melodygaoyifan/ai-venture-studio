# The v3.0.0 design gate — running one loop to a real decision

The roadmap's remaining bar is not code. It is **one product loop run end
to end, ending in a recorded human kill-or-pivot decision at Gate PL5**
(doc 22 §65). Until that happens the outer loop is machinery that has
never been asked to stop anything, and the README says so under Honest
limits.

`avs loop --root launch` is the instrument for it. It reads the
artifacts the stages already write and reports three criteria:

| | requirement | why it is not satisfiable by code alone |
|---|---|---|
| V3-1 | every in-scope stage has a landed artifact | the stages are automated; this one *is* satisfiable by running them |
| V3-2 | a Gate PL5 evaluation exists, run mechanically | `evaluate_kill_criteria` produces it from the PRD's criteria |
| V3-3 | the PL5 record carries a human kill-or-pivot decision | **a human decides.** No agent may write this field for itself |

## Why the system cannot close its own gate

Three rules in the canon collide to make V3-3 human-only, deliberately:

- **Invariant 14.20** — a fired kill criterion cannot be closed without a
  recorded human decision.
- **ADR-U19** — problem selection, scope-tier lock, and roadmap priority
  are human decisions at PL1/PL2/PL5; the system prepares options and
  never chooses.
- **The claim substrate (§20.53)** — a decision record is a claim like any
  other. Writing "we decided to pivot" with no human behind it is exactly
  the fabricated evidence `synthetic_persona_scan` and `claim_lint` exist
  to stop.

So `loop` will report `design gate not met` forever until a person records
a decision. That is the feature. A framework that could mark its own
kill-or-pivot gate satisfied would have no gate.

## Current state of this repo's cycle

Cycle `autoproduct-launch-1` (declared in `launch/cycle.yaml`) entered at
**P2** — the product existed before the loop was pointed at it, so there
was no opportunity to sense and no market to size before building. That
skip is recorded with its reason rather than left as a silent gap; P0/P1
are in scope for cycle 2, whose candidates come from this cycle's PL5
routing.

V3-1 and V3-2 are met. V3-3 is not. The PRD carries **one** axis: the
capability criterion — product-bench build rate below 60% or probe pass
rate below 50% for two consecutive weekly runs — which can fire on the next
weekly run, because `benchmarks/results/*.yaml` is written by the run itself.

A second axis stood beside it from v0.51 until v0.81.0: four consecutive
weeks of over-budget maintenance attention. It was **withdrawn**, not
satisfied ([ADR-033](adr/033-withdraw-weekly-attention-axis.md)). Three
weeks after launch its log held one untracked week and zero logged hours,
because its only instrument was a number someone had to type in weekly — so
what it actually measured was willingness to answer a prompt. The Gate PL5
record of 2026-07-26 stands verbatim with the withdrawal appended beside it;
`launch/gate-pl5-evaluation.yaml` still says what it said at the time, which
is that a criterion cannot fire on data that was never collected, and cannot
be declared safe on it either.

That sentence turned out to describe the surviving axis too. The series
stopped on 2026-07-27 and ran dry for sixteen days without a word, because
the Monday cron entry that fed it never fired and nothing watched it. Since
v0.82.0 the series is a cadence loop of its own — overdue is a finding and a
Discord message ([ADR-034](adr/034-the-bench-is-a-watched-loop.md)). A kill
criterion is only as live as the collection behind it, and "the run writes
it automatically" is a claim about a scheduler, not a guarantee.

The first scheduled run showed the other half of it. One case died on a hung
subprocess and was averaged into the rates as `0.0`, which dropped the probe
rate 22 points and exited 0 — so the number the criterion reads was an
infrastructure failure wearing a capability failure's clothes, and nothing
said so. Since v0.83.0 a rate averages only over cases that produced its
denominator, `cases_measured` / `cases_total` / `unmeasured` are written into
every saved result, and a run that could not measure a case exits **3**
([ADR-035](adr/035-an-unmeasured-case-is-not-a-zero.md)). **Read the
denominator before reading the rate**: 75%-of-three and 75%-of-four are not
the same evidence, and only one of them belongs in a kill decision.

## Closing it, when the criterion fires

1. Run the weekly benchmark. `avs loop --root launch` reads
   `benchmarks/results/` and reports the capability axis inline — the last
   runs' build and probe rates against the floors, and how many consecutive
   runs below them exist. Nothing here needs typing: the series is a
   by-product of the run.
2. If a criterion fires, `loop` exits **3** and says a decision is due.
   Record it in `launch/gate-pl5-evaluation.yaml`:

   ```yaml
   evaluation:
     human_decision: kill        # kill | pivot | continue
     decided_by: <name>
     decided_at: "<date>"
     rationale: >-
       Why, in your own words. This is the artifact the gate is about.
   ```
3. `kill` or `pivot` closes V3-3 and the v3.0.0 gate. `continue` is a
   legitimate decision but explicitly does **not** close it — the gate is
   about the loop's ability to stop, and a continue proves the opposite.

## What a kill actually looks like here

If the capability criterion fires, the PRD's own remedy is scope cut, not
project death (doc 25 §76.4). The honest options at that gate:

- **kill** a scope area — e.g. stop maintaining a lane the benchmark shows
  the loop can no longer build for, and record which one.
- **pivot** the loop's target — e.g. move from broad adoption to a single
  edition, with the other doors frozen.
- **continue** and accept a capability claim the benchmark no longer
  supports, which every subsequent run will keep reporting.

Each is a real decision with a real cost. Picking one is the work the gate
exists to force, and it is yours.
