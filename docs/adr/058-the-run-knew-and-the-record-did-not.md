# ADR-058 — the run knew, and the record did not

**Status:** accepted (2026-08-20)

**Answers:** the six open findings from inspecting run 17's result and run 18's
workspaces. Every one of them is the same defect wearing different clothes:
the system established a fact, and the place that needed the fact did not
receive it.

**Reverses:** nothing. Six repairs to channels that already existed and
carried less than their sender knew.

## Context

Run 17 (2026-08-17) died on credit exhaustion after one case. Run 18
(2026-08-20) completed at build 75%, probes 75%, clean 50%, gate 0%, $67.88.
Inspecting the pair produced seven findings. One — the `24.97s → 0.31s`
test-suite collapse — was checked by re-running the preserved suite
(`35 passed in 0.45s`) and closed as real. The other six are here.

They are not six unrelated bugs. Sorted by shape rather than by file:

| # | The system knew | The reader got |
|---|---|---|
| 1 | run 17's workspaces, on disk | run 18's, at the same paths |
| 2 | this run aborted | a filename |
| 4 | Gate 2 downgraded this, and why | `REQUEST_CHANGES`, cause unnamed |
| 5 | the voter asked for a tool | `None`, which also means "no tool" |
| 6 | this product already exists | a first-FDR readiness bar |
| 7 | this arrangement is illegal | no legal arrangement |

ADR-057 closed a hole of the same family (the cost ledger was complete,
correct, and inside a directory being deleted). This record is that
observation applied six more times, in the six places run 18 exposed.

## Decision

### 1. A preserved workspace is filed under the run that made it

`_preserve_workspace` wrote to `.mas/product-bench/workspaces/<case>` and
`rmtree`'d that path first. So run N's opening act, per case, was to delete
the only copy of run N-1's evidence for that case. The result file kept
pointing at the path, which now held different bytes, and nothing recorded
the substitution. Run 18 destroyed run 17's four workspaces this way — and
run 17 was the credit-exhaustion abort, the run whose forensics were the
reason anyone would look.

Workspaces are now keyed `workspaces/<run-stamp>/<case>`, the stamp is minted
once at the start of the run (not at save time — preservation happens while
the run is still going), and `BenchSummary.run_stamp` carries it so
`result-<stamp>.yaml` and `workspaces/<stamp>/` name the same run by
construction. Disk is bounded by `_prune_workspace_runs`, which drops whole
old **runs**, keeping five.

The point is not that pruning is gentler than overwriting. It is that pruning
is a decision about **age**, which a person can review and change, where
overwriting was a decision about **name collision**, which nobody made and
nobody could see.

### 2. An aborted run says so in its contents

`bench_criterion._scan` excludes an aborted attempt two ways: the
`aborted-*.yaml` glob and a `data.get("aborted")` content check. Two guards
are only worth having when they cover for each other, and run 17's file was
tripping exactly one — the filename. It predates the `aborted:` field
(v0.97.0's `save_summary` had no such key); the abort was recorded by renaming
the file and in no other way. Copy it under a `result-` name, restore it from
a backup that lost the prefix, and it re-enters the capability series as a
build-100% reading over 1 of 5 cases.

The key is backfilled, quoted from the `autopilot_status` the run actually
died on rather than reconstructed as prose, and a test now asserts that
**every** `aborted-*.yaml` on disk carries it.

### 3. Gate 2's reason reaches the row

`test_gate_node` downgrades an APPROVE deterministically when the suite fails
and writes `[Gate 2 blocked — <reason>]` into `leader.summary`. Nothing read
that field. So the one rejection in the system that knows its exact cause at
the moment it decides arrived at the scoreboard as the *worst*-explained one.

Worse than silent: where a voter also happened to be blocked, the row printed
`[1 voter(s) returned no verdict — this is what rejected the task]`. That
claim is derived from `leader.synthesize`'s trigger order, and it is false
whenever something *downstream of the leader* made the call. Run 18's
`01-groupbuy-api t3` is that row — one blocked voter, zero findings, a shape
no path through `synthesize` rejects on.

The marker is now a shared constant (`GATE2_BLOCK_PREFIX`) with a reader
(`gate2_reason`), the reason rides into the bench row ahead of everything
else, and `_blocked_voter_note` gives up its decisive claim when Gate 2 fired.

### 4. `None` stops meaning two things

`Voter._tool_request` returned `None` both for "this response was not a tool
request" and for "this response was plainly a tool request and would not
parse". The caller, reading `None`, handed a tool request to the verdict
parser, which demands `status`/`findings`, raised, and burned every retry
re-sending an identical prompt to get an identical answer. The voter landed
as `BLOCKED_TOOL_FAILURE` with no finding — and two of those on one task is
`len(blocked) == 2`, a `REQUEST_CHANGES` nothing in the code was objecting to.

**Twelve of run 18's seventeen blocked votes were this.** The cause is one
YAML rule:

```yaml
args: {pattern: "def cancel", glob: **/*.py}   # scanner error: bare * opens an alias
args: {pattern: "def cancel", glob: "**/*.py"} # fine
```

A model writing a glob without quotes is the ordinary case, not the exotic
one. This is the same failure the bench probes hit from the other side — a
Python line continuation inside a YAML folded scalar — correct payload,
correct YAML rules, a combination that cannot survive the trip.

`_tool_request` now returns a third answer, `_MALFORMED_TOOL_REQUEST`. The
loop hands the voter its own broken request back with the rule that broke it,
at most twice, then falls through to the old behaviour. `TOOL_PROTOCOL_DOC`
states the quoting rule up front, so the common case never needs the recovery.

This is the review-quality ceiling HISTORY has attributed to severity
calibration for runs 14, 16 and 18. It was a quoting rule.

### 5. A change request is judged as a change

`assess_fdr` asks whether an FDR establishes users, actions, what must exist
and what is out of scope. That is the right bar for a **first** FDR, where
nothing exists and asking is the only way to fill a gap. `run_feature` — the
follow-up path — called the same function with no product context, so the bar
read "everything this request does not mention" as *missing* rather than as
*unchanged*.

All three of run 18's follow-up FDRs came back `needs_answers`, `run_feature`
returned at intake, and the reconciliation gate the increment case exists to
measure never ran. **The increment axis's 0% is not a reading of the gate.**

`assess_fdr` now takes `product_context`; with it, a second system prompt
applies a feature-scoped bar that is explicitly forbidden to re-ask what the
existing requirements already answer, and `run_feature` passes the relevant
slice of the requirement ledger. An unreadable ledger falls back to the strict
bar — degrading toward *more* questions is the safe direction.

ADR-051's shape inverted: there the second call path silently did less; here
it silently demanded more.

### 6. A violation names a legal arrangement

`lane_check` emitted `lane collision: t1 (api) and t3 (orders) both expect
'app/models*.py'` and stopped. True, precise, and unactionable: it names the
arrangement that is forbidden and never one that is allowed. Run 18's
`03-groupbuy-auto` was handed that sentence three times, produced a materially
identical plan each time, exhausted `MAX_REVISIONS`, and was blocked at Gate
U2 having built nothing — the whole case, and the run's build rate.

The same product's `01-groupbuy-api` had already solved the identical
collision by hoisting the shared model file into its own task both others
depend on. That remedy was available throughout and no message mentioned it.

The check now names three remedies — HOIST, MERGE, SPLIT — concretely, with
the actual task ids and globs, and reports once per colliding **pair** rather
than once per glob pair.

## What stays out

- **No cap on preserved runs below five, and no pruning by size.** The thing
  that just cost us run 17's forensics was a bound nobody could see. Five
  runs is enough for the cross-run comparison the last four investigations
  each needed.
- **No auto-repair of a malformed tool request.** The voter is told the rule
  and re-sends; nothing rewrites its YAML for it. A quoting fix applied by
  the harness would hide how often models get this wrong, which is the number
  worth watching.
- **No weakening of the first-FDR bar.** It is right where it is applied. The
  feature bar is a second bar, not a lowered one.
- **No remedy chosen for the planner.** `lane_check` lists three legal
  arrangements and says "whichever matches what the work actually is". A
  checker that picks is a planner.
- **No rewriting of run 16's or run 18's recorded rates.** They are what those
  runs measured. What changes is what the *next* run can measure.

## What keeps this honest

- `tests/test_bench_keeps_its_evidence.py` writes two runs' workspaces for the
  same case and asserts the first run's bytes are still readable — the exact
  assertion whose absence let run 18 delete run 17.
- `test_every_aborted_file_on_disk_says_so_in_its_content` walks the real
  `benchmarks/results/` directory. A guard that is redundant in the reader and
  not redundant on disk is not redundant.
- `test_the_marker_the_writer_writes_is_the_one_the_reader_reads` reads the
  source of `test_gate_node` and fails if the marker goes back to being an
  inline f-string. The drift is what this replaced.
- `test_the_run_18_request_really_is_unparseable` asserts the premise rather
  than assuming it: the recorded payload raises, and the only difference from
  a working one is the quotes.
- `test_run_feature_hands_over_the_existing_requirements` checks the wiring,
  not the capability. A parameter nobody passes is ADR-048's inert instrument
  one layer down.
- `test_the_remedy_the_message_recommends_actually_clears_the_check` runs each
  of the three suggested arrangements back through `lane_check`. Telling a
  planner to do something the checker still rejects would be worse than saying
  nothing.

## The lesson worth keeping

Five of these six were **visible in the run's own artifacts** and invisible in
its record. The Gate-2 reason was in `leader.summary`. The malformed tool
request was quoted verbatim in every blocked voter's `notes`. The intake
questions were in `FDR-QUESTIONS.md`. The lane collision named its own globs.
Run 17's abort reason was in case 02's `autopilot_status`, one line above
where the `aborted:` key belonged.

Nothing had to be discovered. Each fact had been written down by the component
that established it, into a field, in a file, that no downstream reader
opened. The work was not measurement — it was delivery.

When a component knows why it did something, find out who reads that. If the
answer is nobody, the system does not know it.
