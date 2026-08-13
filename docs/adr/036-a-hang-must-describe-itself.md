# ADR-036 — a hang must describe itself, and must not outlive its own timeout

**Status:** accepted (2026-08-13)

**Answers:** ADR-035's last bullet — *"why case 04's suite hangs is still
unknown"* — which was left open on purpose, as a blocked task with a named
reason rather than a diagnosis invented on the spot.

**Reverses:** nothing in ADR-035. The rates, the denominators, the exit-3
rule and the blocked-gate conversion all stand exactly as written.

## Context

Bench run 12's case 04 died on `pytest -q` exceeding 300s. ADR-035 made
that a blocked gate instead of a dead run — the right call, and it left the
cause for the next run to reveal.

The next run never could have revealed it, because **the harness deleted
the evidence four separate ways**, and every one of them is upstream of the
product:

1. **The timeout report kept only the command line.** `TimeoutExpired`
   carries `output` and `stderr` — it was holding what the suite printed
   the whole time, and the report threw it away and said 300s had elapsed.
2. **Nothing asked pytest where it was stuck.** pytest will name the hung
   test and the exact line it is blocked on if `faulthandler_timeout` is
   set; it was not.
3. **The killed suite's children survived it.** `subprocess.run`'s timeout
   path signals the direct child alone. A product whose tests boot a server
   left that server running, holding its port against the next case and
   holding the stdout pipe it inherited.
4. **The crashed case was the only one whose workspace was deleted.**
   Preservation ran after the autopilot call, so an exception jumped over
   it into the `finally` that removes the temp directory. `run_case`
   preserved the workspace of every failure *except* the failures that
   needed it, and run 13 then overwrote the directory name. **The product
   that hung no longer exists anywhere.**

So run 12's specific product cannot be root-caused, and saying so is part
of this record. What can be established is the shape of failure that
satisfies every gate the framework had and still hangs forever.

## The cause the gates could not see

The web profile's boot contract says the entry point must serve when run
directly — `python app/main.py`. A module-level `uvicorn.run(app)`
satisfies it completely. The boot gate boots the entry and watches for a
listening socket, and a top-level serve call passes that gate first try.

But `import main` is what every test does, and that same line never
returns. pytest collects, blocks inside the import, prints nothing, and
five minutes later the process is killed with no output — which is exactly
what run 12's row recorded.

Two gates that each pass, whose conjunction is a permanent hang. The
contract already said to put the serve call under `if __name__ ==
"__main__":`; nothing enforced it, and enforcement by *running* the code is
the hang itself.

## Decision

1. **A timeout report carries what the process printed.** `_hang_detail()`
   puts stderr and stdout into `TestReport.detail`, clipped from both ends
   — the faulthandler frame naming the test is the *first* line of a dump,
   so a tail-only clip keeps the plumbing and drops the answer.
2. **Every test command runs with `faulthandler_timeout` set** (120s,
   below the 300s kill). It only prints, so a merely slow suite is
   unaffected; a stuck one names itself while still alive to write it.
3. **A timeout kills the process GROUP.** Every runner that boots a
   product — the test gate, the docker sandbox, the probe runner, the boot
   gate, the screenshot server, and the generated probe frame — starts its
   child in its own session and signals the whole tree.
4. **The case that crashed keeps its workspace.** `run_case` preserves it
   on the exception path and carries the path out on the exception, so the
   error row points at the evidence instead of naming a failure whose
   evidence is already deleted.
5. **A module that serves on import is rejected before the suite runs.**
   `_blocks_on_import` is a parse, not an execution: a blocking serve call
   at module level fails the build with feedback naming the file, the line,
   and the fix. Static, because the dynamic form of this check is the bug.

## What keeps this honest

- `test_a_hung_suite_reports_which_test_hung` runs a real suite that really
  hangs and requires the hung test's *name* in the report. It failed
  against the first version of decision 1, which clipped the dump from the
  wrong end — the test caught the fix being wrong before the bench did.
- `test_a_killed_suite_does_not_leave_a_server_running` and
  `test_a_wedged_probe_does_not_leave_the_product_server_running` spawn a
  real grandchild and `pgrep` for it after the timeout.
- `test_the_crash_row_points_at_the_preserved_workspace` reads the row the
  bench actually writes, not the helper.
- `test_preserving_the_workspace_never_replaces_the_real_failure` — the
  forensics path must not be able to report a bookkeeping error where the
  real failure was.
- `test_the_import_gate_does_not_flag_ordinary_module_level_calls` — `.run`
  is everywhere; a gate that fires on `subprocess.run` at module level
  would block correct products, and a blocked correct product is a worse
  failure than the one being fixed.
- **Stated rather than left implied:** none of this proves what run 12's
  case 04 did. It is unrecoverable. What is claimed is narrower and
  checkable — the next hang arrives with the test name, the line, its
  output, and a workspace on disk.
