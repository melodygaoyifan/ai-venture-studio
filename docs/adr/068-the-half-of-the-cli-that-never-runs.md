# ADR-068 — the half of the CLI that never runs

**Status:** accepted (2026-08-22) · **Release**: none (tests and docs only)

## Context

ADR-067 asked, of 382 forensic tests, "would you still fail if the fix were
taken away?" and answered it by mutation. It closed at 382 killed, 0 survived —
but the method does not generalise. Asking it of the whole suite is thousands of
mutants times a multi-minute suite, and the repo is 19,419 statements.

The **complement** scales, and it points the same sound direction. ADR-055 wrote
the premise down:

> A test proves that the code it calls works. It says nothing whatsoever about
> code that no test calls.

A line the suite never executes cannot be pinned by any test in it. No mutation
of that line can fail anything, so it is a guaranteed survivor — established for
100% of `src/` at the cost of **one suite run** instead of a rung of a ladder.

There was a second reason to run it. ADR-054 closed on a sentence that is a
claim about a population, established from a sample of one:

> Nothing caught it because **no test invoked the command**. `evaluate()` has
> coverage; the CLI path around it had none.

Ten orphaned lines sat inside `avs bench-criterion`, below the `typer.Exit` that
fires when the kill criterion fires, so the command raised `NameError` on every
run where the project was **healthy** and worked only when it was not. It
survived eleven recorded bench runs. Nobody had asked how many of the other 77
commands are in the same position.

And ADR-055 itself had declined a coverage gate on an estimate:

> several hundred legitimately-untested branches

Nobody had counted them. The whole subject of ADR-066 and ADR-067 is a
denominator that was asserted rather than measured.

## Decision

**Measure it, and ratchet the one number that can only get worse by accident.**

### What the first coverage measurement of this repo says

Run over the full suite — 2432 passed, 3 skipped, matching PC-1 exactly, with
`.venv/bin` on `PATH` so the six `ruff`-gated tests do not silently skip:

| | |
|---|---|
| statements | 19,419 — **2,315 never executed (88.1% executed)** |
| branches | 6,048 — **703 partial** |
| functions with zero executed statements | 93 |
| `cli.py` | **929 of 1,856 statements never execute (46.2%)** |
| files measured | 201 |

ADR-055's "several hundred" was low by roughly an order of magnitude. That
*strengthens* its reasoning for declining a percentage gate and invalidates its
number, and both halves are recorded here rather than quietly corrected.

### ADR-054's sentence, asked of all 78 commands

Two detectors, sharing no mechanism, as ADR-052 required of the dead-account
check ("the second because the first stops firing the day the provider rewords
its errors"):

* a **runtime** trace — did any statement in this command's body execute during
  the suite?
* a **static** scan of test sources — does any test file so much as type this
  command's name inside a string?

They disagree in one direction only, and the disagreement is fully explained:
the static scan undercounts by exactly the six commands driven through the
parametrised `CLI_GATED` table in `test_use_case_matrix.py`, which builds argv
as `[*argv, "--repo-dir", ...]`. **Zero contradictions in the other direction.**

> **42 of 78 commands have never been entered by any test. 30 are not so much
> as named in one.**

Among the 30: all four human judgment gates — `brief-approve` (U1),
`plan-approve` (U2), `spec-approve` (U3), `scr-approve` — and both commands that
act on the world, `automerge` and `deploy-execute`.

That reads worse than it is, and the distinction is ADR-054's exactly. The
*logic* beneath those commands is tested: `src/ai_venture_studio/policy.py` runs
at 94%, and the ADR-031 refusal path — no armed, human-authored, expiring policy
file, no action — is covered. What is unmeasured is the CLI wrapper, which is
the precise layer ADR-054's defect lived in, ten lines below where `--help`
stops looking.

### Running them, because reading them is not the same thing

ADR-054's other lesson is that `--help` proves only that Typer could build the
parameter model. So all 42 unreached commands were **run**: no arguments, an
empty temporary directory as `cwd`, `HOME` redirected into it, and
`ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `GH_TOKEN` / `GITLAB_TOKEN` /
`AUTOPRODUCT_SENTRY_TOKEN` stripped, so a command needing credentials takes its
refusal path instead of its network path.

| verdict | n |
|---|---|
| refused cleanly (`typer.Exit` / `UsageError` / missing input) | 37 |
| long-running (`bench`, `review-gate`, `serve`, `setup-tests`, `worker`) | 5 |
| **broken** (`NameError`, `AttributeError`, `ImportError`, …) | **0** |

A sound negative: there is no second ADR-054 in the CLI **today**. The exposure
is that nothing would notice the next one.

### The 93 dead functions, triaged

Bucketing by name reference collapses fast, and the collapse is the interesting
part:

* **10** are `@_register(...)` MCP stage tools in `mcp/stage_tools.py`. Name-based
  reachability cannot see decorator registration. They are **reachable by an
  agent at runtime and entered by no test** — unmeasured, not dead, and a
  sharper finding than the one the bucket claimed.
* **1** was refuted outright: `ToolBox._list_files` is reached through
  `getattr(self, f"_{tool}")(**args)`. Tested by hand: it works and honours the
  documented `glob` default `**/*`.
* **6** are genuinely dead — no reference in `src/`, `tests/`, `scripts/` or any
  string, no re-export, no dynamic dispatch. Two of them are more than dead
  weight:

  * `upstream/verdicts.py:is_terminal` — the docstring describes a three-way
    partition (approvals, escalations, BLOCKED); the body is
    `return verdict in ALL_VERDICTS`, the union of all three, which is `True`
    for every known verdict. A name promising a partition its body does not
    make. Harmless only because nothing calls it.
  * `lanes/calibrate_perf.py:record_calibration` — the **only** writer anywhere
    in the repo of `benchmarks/perf_seeded/calibration.yaml`. The lane is *not*
    stuck at PROVISIONAL, because that file is committed from the 2026-07-26 run
    and `lane_status()` reads it. The narrower true fact: the committed artifact
    **cannot be regenerated by any code path**, only hand-edited.

  The other four are `github.py:post_pr_comment`, `github.py:pr_head_branch`,
  `profile_schema.py:load_structured_profile`, and `tools/wireup.py:wireup_diff_gate`
  — the last of which is a dead *alias*, not a dormant gate: `wireup_check` is
  wired into every build at `upstream/build.py:1464`.

### The ratchet

`tests/command_never_run.py` does the audit; `tests/test_a_command_no_test_types.py`
holds the ledger. A new `@app.command()` that no test names fails the suite.

`KNOWN_UNTYPED` is **debt, not justification** — the opposite of
`test_write_without_reader.py`'s allowlist, and it says so. It is frozen at the
30 measured here so the set can only shrink, and each entry records what the
command is, so a reader can tell an approval gate from a `dbt` wrapper.

The bar is the weakest one available on purpose: not invoked, not asserted on —
*typed*. A weak bar makes the negative unarguable, and it means the scan's known
blind spot (a name built from a parametrisation) resolves toward "covered"
rather than toward a false accusation.

## Consequences

The instrument had to defuse a trap the repo has already paid for once. ADR-060
found that a test's own allowlist of field names was **supplying every reader it
then asserted existed**, because a bare string literal counted as a read;
deleting the real readers left it green. The same trap sits in a ledger of 30
command names, in a file under `tests/`, read by a scan that counts any string
literal — unexcluded, it reports zero unnamed commands forever.

So the ledger excludes itself, and `test_the_ledger_is_not_its_own_evidence`
asserts the trap rather than the fix: scanned *without* the exclusion, every
entry must vanish. It does. Plus the two guards ADR-067 earned — a synthetic
tree where the answer is known, and a refusal to report from a moved `cli.py`,
an unrecognised decorator, or an exclusion that swallows the suite. An empty
measurement reads exactly like a passing one.

`git diff src/` is empty. Version stays `0.111.0`, and PC-1 moves 2432 → 2437.

**Paid down the same day, 30 → 24.** A ratchet that only stops the number
growing leaves the measured debt sitting there, and the six worst entries were
the four human judgment gates and the two commands that act on the world.
`tests/test_the_gates_a_human_touches.py` now drives all six through
`CliRunner`, and a coverage run over that file alone confirms they are
**entered**, not merely named: `brief-approve` 3/4 body statements,
`spec-approve` 3/4, `scr-approve` 3/4, `plan-approve` 6/7, `automerge` 23/32,
`deploy-execute` 24/32. The unexecuted remainder on the last two is the point —
`--dry-run` returns before `forge.merge` and before
`subprocess.run(policy.command)`, so the suite enters every line of the
decision and none of the action.

What that bought is specific, and it is not the policy rules, which
`test_automation.py` already pinned. It is the seven lines of wrapper around
them: `plan_approve` reaching through `plan_result.tasks` and then `t.id` and
`t.description`; `scr_approve` indexing `data['spec_slug']` and
`data['reason']`; `automerge_cmd` pulling four nested keys out of a final YAML
and passing them to `evaluate_merge` by keyword. A test of the callee says
nothing about any of it, and a return shape that moves breaks all of it
silently. Two defects in the tests themselves surfaced on the way and were
fixed in the same change — a `Dockerfile` chosen as an innocuous deploy file
when it is in `ALWAYS_HUMAN_PATHS` (now its own assertion, that no policy can
arm one), and a rich-wrapped console line asserted raw, which would have made
the test pass or fail on terminal width. PC-1 2437 → **2451**.

## What stays out

**A coverage percentage gate.** ADR-055 declined one and this measurement
makes its case stronger, not weaker: a number that must not fall is a number
people raise by testing what is easy.

> **Corrected the same day.** The sentence that stood here was *"2,315
> unexecuted statements is not a queue of defects, it is mostly error branches
> nobody has arranged to hit."* That is an assertion about a denominator,
> written by someone who had counted the denominator and not looked inside it
> — in the record that condemns exactly that move. So it was measured. Every
> unexecuted statement, bucketed most-specific-first:
>
> | bucket | n | share |
> |---|---|---|
> | DEAD-FUNCTION (in a function with zero executed statements) | 749 | 32.4% |
> | **ORDINARY** (a live function, on a path never taken) | **621** | **26.8%** |
> | GUARD-RETURN | 322 | 13.9% |
> | EXCEPT-BODY | 269 | 11.6% |
> | UNATTRIBUTED (continuation lines, decorators) | 221 | 9.5% |
> | RAISE | 115 | 5.0% |
> | IMPORT | 18 | 0.8% |
>
> Error-ish — except bodies, raises, guard returns — is **706, or 30.5%**.
> "Mostly" was wrong. The largest bucket is dead functions, and the second is
> ORDINARY, which is ADR-054's shape precisely: ordinary lines in a command
> the suite does enter, below a branch it never takes. 221 of those are in
> `cli.py`.
>
> It softens on inspection without disappearing. ORDINARY's single most
> common leading token is `console.print` (108 of 621), and report-row
> appends account for much of the rest — presentation code on unexercised
> branches, not silent logic. That is a reason not to panic, not a reason to
> have asserted it. The conclusion survives the correction; the sentence that
> reached it did not, and the difference is the whole subject of ADR-066 and
> ADR-067.

**Deleting the six dead functions.** That is a `src/` change and therefore a
visible decision, not a tidy-up to fold into a test-only commit. **Taken in
ADR-073 (v0.115.0)**: five deleted, and `record_calibration` wired up instead —
it was the one with behaviour attached. `is_terminal`
in particular wants a judgment — whether the partition its docstring describes
should exist, or the function should not.

**A ratchet on the 93 dead functions or the 10 unmeasured MCP tools**, and for
the MCP registry the reason is measured rather than budgeted. The cheap
detector has **zero discrimination there**: all 18 `@_register` tools are
already "named by a test", so a static ratchet reports nothing on day one and
keeps reporting nothing — an empty measurement wearing a green tick. The
names match for the wrong reason, which is ADR-064's lesson in new clothes:
`tests/test_deploy.py` names `migration_scan`, but that is the well-tested
function in `deploy/probes.py`, not the registered wrapper around it; and
`run_tests` is matched by `guard.authorize("run_tests", 2)`, a risk-level
assertion that never calls the tool. A name is a weak key. Measuring this
surface honestly needs the coverage run, and the CLI ratchet is the one that
pays for itself in 0.36s against source text alone, because it guards the
layer where the defect that cost eleven bench runs actually lived.
