# ADR-076 — the disk already knew

**Status:** accepted (2026-08-25) · **Release**: v0.118.0

## Context

ADR-075 closed the six run-19 findings its debug reached; this ADR closes
the four it did not — found by continuing the same forensics over the same
preserved artifacts (the run's five workspaces, the result rows, the source
of every gate involved), per the standing direction: debug, push, deploy,
no new instruments. The common shape, and the title: in every one of the
four, the truth was already on disk, and the machine either re-derived it
badly or read a stale copy instead.

Three are code defects fixed here; the fourth is an attribution correction
to a record ADR-075 had just corrected once already.

## The four findings

### 1 — Gate 2 re-applied a diff whose post-image was already committed

Case 03 t4 (团购汇总端点, workspace commit `55fa6a4`):
`[Gate 2 blocked — error: reviewed diff did not apply cleanly to HEAD]`.
Reproduced exactly: the task committed a sqlite file (`data/app.db`)
alongside the code, `git diff` without `--binary` describes a binary file
as a stub line, and `git apply --3way` refuses a stub outright ("cannot
apply binary patch to 'data/app.db' without full index line"). The gate
was rebuilding, from lossy text, a tree the repository already held: for a
committed range the worktree at the range's tip **is** the post-image.

Fix (`testing.py`, `orchestrator/graph.py`, `state.py`): `range_tip()`
resolves a local `A..B`/`A...B` target to its tip commit; `run_test_gate`
takes `checkout=` and, when given, adds the worktree at that commit and
skips the apply. `test_gate_node` uses it for any range target whose diff
was fetched from that same target; a caller-supplied diff (`diff_text`,
now flagged `diff_supplied` in the state) has no committed tip to trust
and keeps the apply path unchanged, as do PR/MR URLs and single revisions
(whose post-image is the working tree, not a commit).

### 2 — the retry's own review read yesterday's row

Case 05 t5: the recovered task's review carried a critical finding —
"Shipped implementation for t5 contradicts outcomes.yaml's own
'build_failed / workspace reset' record" — and the repair machinery rolled
a genuine recovery back over it. The reviewer was right about the disk and
wrong about the world: `_retry_failed_tasks` recorded the retry's outcome
only *after* `_attempt_task` returned, and the review runs *inside* the
attempt, so the row it read described the first attempt as if nothing had
happened since.

Fix (`upstream/autopilot.py`): before the attempt, the task's row is
rewritten in place — same truthful `build_failed` status, detail extended
with "(auto-retry in progress — the change under review may already
implement this task; this row records the first attempt)" — and persisted.
The transient note never outlives the attempt: the final row replaces it
on success and failure alike, and the first attempt's diagnosis still
travels in both.

### 3 — `built` requirements citing five test files nobody ever wrote

The same task's ledger: `product/requirements.yaml` rows with
`status: built` whose `verified_by` names `tests/test_complete_*.py` —
five files absent from the workspace, never committed, never written.
Mechanism: on an established product the pre-existing suite keeps the
build gate green even when the implementer writes no tests at all (t5
shipped `app/db.py` + `app/handler.py` on 39 old green tests), and
`sync_ledger` — correctly following the spec (ADR-045) — then cites the
spec's declared skeleton paths as the criteria's proof. ADR-075 F fixed
the prompt that invited this; this fix makes the class impossible
regardless of prompt obedience, at the source rather than in the ledger:
a ledger that filters out the lie would still leave a `built` spec whose
proof does not exist.

Fix (`upstream/build.py`): after the suite passes and before the save,
any spec-declared skeleton path missing on disk is a gate failure whose
feedback names the missing files; the next iteration writes them or the
build honestly fails. Mechanical stop, machine's job — no judgment in it.

### 4 — the "honestly discarded" repairs were condemned by the shadowed suite

ADR-075's corrected HISTORY.md note kept one run-day sentence: "several
repairs were attempted and honestly discarded because they broke the
suite." That sentence is ADR-075's own defect B at a second call site.
The repair gate (`_fix_iteration`) runs the same host-subprocess suite
the review gate runs, so the eight `repair attempted, not applied: the
repair broke the suite (failed)` rows in cases 02–05 were read through
the same site-packages `tests` shadowing. Reproduced on the preserved
case-02 workspace: the bare host suite reports 4 collection errors; the
v0.117.0-shielded `_pytest_in_subprocess` reports 31 passed. Whether each
discarded repair was actually good is unknowable now; what is known is
that the gate that condemned them was importing the wrong package.

No code change — the shield lives inside the shared function, so the
repair gate has been covered since v0.117.0. The correction is recorded
in place in `benchmarks/results/HISTORY.md` and the PC-19 note; no rate
is re-scored.

## Controls

Every test failed against the pre-fix tree before passing against this
one (`git stash push -- src/`): the gate tests fail on the absent
`checkout` seam, the retry tests fail with no outcomes file written at
attempt time, and the skeleton test fails exactly the run-19 way — built
after one implementer call, no tests written. One existing test
(`test_build_retries_after_refused_write`) assumed a provider could build
without ever writing the spec's declared skeletons; under the new contract
its provider now writes them, and everything it actually pinned — a
refused write is feedback with a bounded retry, never a fatal error — is
asserted unchanged.

## Consequences

- A committed-range review can no longer be blocked by its own binary
  files, and Gate 2 for the autopilot's `HEAD~1..HEAD` reviews tests the
  exact commit the voters judged.
- A review that runs mid-retry reads a record describing the present.
- `status: built` now implies every cited proof file exists on disk at
  save time.
- The run-19 clean-rate decomposition loses its last product-blaming
  clause: the repair discards join defects A/B on the harness side.
