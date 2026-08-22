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

## What stays out

**A coverage percentage gate.** ADR-055 declined one and the measurement makes
its case stronger, not weaker: 2,315 unexecuted statements is not a queue of
defects, it is mostly error branches nobody has arranged to hit. A number that
must not fall is a number people raise by testing what is easy.

**Deleting the six dead functions.** That is a `src/` change and therefore a
visible decision, not a tidy-up to fold into a test-only commit. `is_terminal`
in particular wants a judgment — whether the partition its docstring describes
should exist, or the function should not.

**A ratchet on the 93 dead functions or the 10 unmeasured MCP tools.** Both
would need the coverage run in CI, and the audit above runs in 0.36s against
source text alone. The CLI ratchet is the one that pays for itself, because it
guards the layer where the defect that cost eleven bench runs actually lived.
