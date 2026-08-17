# ADR-051 — a second path is not a weaker one

**Status:** accepted (2026-08-17)

**Answers:** a read-through audit of the build spine and the tool boundary,
asking of every control *does it hold on the path most runs actually take?*
Twice it did not, in the same shape: a control implemented once, on one of
two paths, with the other path looking identical from the outside.

**Reverses:** nothing. Both halves restore a guarantee the system already
claimed to make.

## Context

ADR-037 fixed one instance of "one concept, two definitions" and ADR-038
generalized it to the class. This is the sibling class: **one control, two
call paths.** It is harder to see, because nothing is written twice — the
second path simply does less, and its output has the same type as the
first's.

Two instances, found by reading rather than by a failure:

1. **`--parallel` builds were never reviewed.** `_attempt_task` — the
   sequential path — runs spec → build → `review_and_repair`.
   `_build_wave_parallel` runs spec → build → merge, and recorded
   `TaskOutcome(status="built", review_verdict=None)`. Every parallel task
   reached `outcomes.yaml`, the bench scoreboard and the founder's report
   claiming a successful build with no verdict, in the same table as
   sequential tasks that carried a real one. It also dropped `iterations`,
   `files_written` and `test_summary`, the three fields ADR-042 added so a
   row carries its diagnosis.

   This is precisely the hole `retry-task` shipped with — the hole
   `review_and_repair` was extracted to close, whose docstring already says
   *"A retry is not a lesser build."* It survived in the wave loop because
   that loop is hand-written rather than routed through `_attempt_task`,
   which is the same reason the retry paths were wrong before they were
   merged.

2. **ADR-U03 taint isolation was off on the default transport.**
   `build_toolbox` chooses between `MCPToolBox` (subprocess partitions) and
   `ToolBox` (in-process). It passed `voter` and `risk_ceiling` to the first
   and dropped both on the second, and `ToolBox` constructed no `TaintGuard`
   at all. `tool_transport()` returns `in_process` unless
   `AUTOPRODUCT_TOOL_TRANSPORT=mcp` is set, so the guarded branch is the one
   almost nobody takes.

   Nothing was exploitable. `VOTER_TOOL_REGISTRY` is four read-only,
   repo-scoped tools, every one of them L0, so a tainted session had no L1+
   call to make and no ceiling had anything to refuse. **That is the
   finding.** The guarantee held because the table was short, not because
   anything checked it, and the first person to add an L1 tool to that
   registry would have removed the guarantee without touching a line of
   security code. `mcp/toolbox.py` already carries a comment recording that
   this exact pair was "implemented on both sides and never connected" once
   before.

## Decision

1. **`_build_wave_parallel` calls `review_and_repair`**, serially, after each
   merge, with the same arguments the sequential path uses, and carries the
   full outcome record. Only the *build* is parallel; what `--parallel` buys
   is wall-clock on the writers, never a weaker gate.

2. **The merge carries its own bookkeeping.** Adding a review to that path
   made an existing latent bug reachable: `finalize_build_bookkeeping` ran
   *after* the merge commit and left `built: true`, the changelog fragment
   and the ledger sync uncommitted — and `_fix_iteration`'s rollback runs
   `git checkout -- .`. `build.py` had already learned this one commit
   earlier ("BEFORE the commit, not after"), where the cost was a resumed run
   re-paying for modules it had already built. The merge is now
   `--no-ff --no-commit`, bookkeeping is written into the merge commit, and
   the commit is created afterwards — so `HEAD~1..HEAD` is still the whole
   merged branch, which is what `_review_head` reviews.

3. **`ToolBox` enforces the ceiling and the taint**, at the same two points
   `MCPHost` does and against the same tables (`SERVER_RISK`, `server_for`):
   the ceiling filters the allowlist at construction, and every call is
   authorized and every result observed for a research wrapper. A denial is
   returned as data like every other tool failure — a voter can degrade on
   one, a raise would take down the review — and does not spend the budget,
   because a refused call is not a call.

4. **`build_toolbox` passes the same four arguments to both branches.** A
   switch whose two positions differ in their security properties is not a
   transport switch.

5. **`ROLLBACK_SEVERITIES ⊂ ACTIONABLE_SEVERITIES` is checked at import**,
   not only in the suite, and `ACTIONABLE_SEVERITIES` is frozen. ADR-038
   pinned this relation with a test, which catches drift at CI; an edit made
   and run without the suite is the case a test cannot reach, and this file
   is edited by the same machine it drives. §11.19's rule applies to the
   framework's own constants: enforcement at load time is the only reliable
   form of the control.

## What stays out

The two build loops are **not** merged into one. `_build_wave_parallel`
prepares and commits specs on main before branching, and `_attempt_task`
does not; forcing a shared body would put a `parallel` flag through the
middle of the one function whose job is to be the same every time. What is
shared is the reviewed tail, which is the part that was missing.

`state.CLEAN_VERDICTS` and `automation.MERGEABLE_VERDICTS` stay separate —
ADR-038's rule is unchanged: unify definitions of one concept, name and pin
definitions of two. This ADR adds the third case: **route both paths through
one implementation of a control.**

## What keeps this honest

- `test_a_merged_parallel_task_is_reviewed` and
  `test_the_diagnosis_fields_survive_the_merge` — the behaviour.
- `test_the_bookkeeping_is_inside_the_merge_commit` asserts a clean working
  tree after the wave *and* that the merge kept both parents, so a future
  "simplification" to a squash cannot silently narrow what gets reviewed.
- `test_both_build_paths_route_through_the_same_review` scans both loops'
  source with comments stripped, so a third build path added later fails
  this test rather than shipping unreviewed.
- `test_a_conflicted_merge_is_still_not_reviewed` and
  `test_a_failed_build_is_not_reviewed` — the negative half: work that never
  landed must not be recorded as though it had.
- `test_in_process_is_the_default_transport` — if that ever flips, the rest
  of the transport suite is guarding the rare path.
- `test_every_voter_tool_is_l0_so_the_default_ceiling_admits_them_all` turns
  the property the old code was accidentally relying on into an assertion:
  an L1 tool joining the voter registry now fails a test instead of quietly
  becoming unreachable.
- `test_a_tainted_run_loses_l1_tools_in_process`,
  `test_l0_still_works_after_taint`,
  `test_tool_output_carrying_research_taints_the_run`,
  `test_taint_is_one_way`.

Both new suites were run against the previously released build as a control:
4 of 6 and 7 of 8 fail there and pass here. The ones that pass in both are
the negative-path tests, which is what they should do.

**Not claimed:** that anything was exploited, or that clean-review rate
moves. (1)–(2) mean `--parallel` and the default sequential path now produce
the same *kind* of record, which changes what run 17's scoreboard is
measuring if any case uses lanes; (3)–(4) close a control that was never
load-bearing yet; (5) is a guardrail against a future edit.
