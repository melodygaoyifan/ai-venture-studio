# ADR-067 — a test that could not have failed

**Status:** accepted (2026-08-21) · **Release**: none (tests and docs only)

## Context

ADR-066 shipped, and then its own control was run: restore the one line the
change had replaced, run the suite, read the tests that did *not* fail. One did
not — `test_a_skipped_case_is_not_scored_as_a_zero`, named for a row type, green
on the build where that row type did not exist. It was fixed in `7646b03`.

Asked to close the class rather than the instance, the question became: **how
many other regression tests in this repo are green against the build they were
written to condemn?** Twenty-six test files in `tests/` are named by an ADR as
its mechanism. Every one of them is a claim, written down in a test name, about
a defect someone had already seen. None of them had been checked as a
population.

Reading them would not answer it. ADR-054's lesson — running the thing beats
reading it — applies to tests too, and more sharply: a test's whole purpose is
to fail on some build, and the only way to know which build is to run it there.

## Decision

**A regression test's claim is falsifiable or it is decoration, and the repo now
knows which of its own are which.**

The control is mechanical and needs no judgement per ADR. For each forensic test
file, find the commit that added it, check out that commit's **parent**, and run
today's version of the test file against it. Anything that passes is a test that
would not have caught the defect it is named for.

    git worktree add <scratch>/ctl <parent-of-adding-commit>
    cp tests/<file> conftest.py <scratch>/ctl/...
    cd <scratch>/ctl && PYTHONPATH=$PWD/src <repo>/.venv/bin/python -m pytest tests/<file>

**Roll back the whole tree, not just `src/`.** The first pass swapped only the
Python source via `PYTHONPATH` and ran from the current checkout, and it was
wrong in both directions. `test_a_green_run_that_published_nothing.py` reported
**7 passed** — a total hole, apparently — because ADR-065's subject is a GitHub
workflow and a shell script, and those were being read from the *current* tree
no matter which `src/` was on the path. Under the honest control it fails, as it
should. `test_every_probe_compiles.py` moved the other way for the same reason:
its parametrization is over probe *data*. A control that leaves half the
artifact in place measures nothing reliably; it just happens to agree sometimes.

## Consequences

Twenty-six files ran. Fifteen produced a verdict; **forty-four tests passed
against the tree that predates them.**

Most of that is correct and must stay correct. A guard that is narrow on purpose
has a half that passes on both builds by construction — `..._is_still_...`,
`..._is_left_alone`, `..._is_not_a_collision`. Those are not weak tests; they are
the half that stops an over-broad fix, and their control is a *different*
mutation. `test_s607_is_enforced_and_not_ignored_anywhere` passes on a tree from
before the ignore was ever added, which is exactly right — it guards the entry
coming back. `test_a_line_continuation_cannot_survive_the_folded_scalar` records
a fact about YAML and Python, not about this repo, and would pass on any build
ever written.

**Five were real.** Each was named for behaviour it did not pin:

| Test | Why it was green on the broken build |
|---|---|
| `test_the_newest_run_is_last_even_beside_an_abort` (ADR-054) | asserted only `runs[-1]`. `aborted-` sorts before `result-`, so the misplaced file went to the FRONT and the newest *result* stayed last either way — the defect was invisible at the position the test looked. |
| `test_the_cadence_reports_nothing_rather_than_zero` (ADR-053) | hand-wrote `build_rate: null` into a file and asserted the reader said nothing. Pre-fix, `float(None)` raised `TypeError` and the `except` returned `""` — the right answer by accident. The defect was in the WRITER, and no reader can decline to print a zero it was handed. |
| `test_folding_keeps_the_worst_severity_any_site_was_raised_at` (ADR-039) | with no folding at all, nine findings come back and the HIGH one is first anyway, because the list is ordered by severity. `findings[0]` was green on a build with nothing to soften. |
| `test_the_floor_diagnostic_can_actually_be_printed` (ADR-055) | rendered its **own copy** of the message and asserted the copy came out. The shipping message lives in `test_use_case_matrix.py`; a `NameError` put back there would not have failed anything. |
| `test_the_remedy_the_message_recommends_actually_clears_the_check` (ADR-041) | proved three arrangements clear the check, and never read the message. The link to what the message *recommends* was a comment: `# (1) HOIST`. |

All five now fail against their own pre-fix tree, verified individually.

Two of the five deserve to be read together, because they are one shape and it
is this repo's most persistent one. ADR-055's guard kept a second copy of the
message it was guarding; ADR-041's kept the remedy names in a comment beside the
message that names them. **A test that restates a fact instead of reading it is
ADR-051's defect wearing a regression test's clothes** — two copies, and the
guard watching the one it owns. Both now read the shipping artifact: one lifts
the f-string out of the source with `ast` and evaluates it for every stage and
rung, the other asserts the collision message still offers all three remedies
the cases below prove out.

**Eleven files are unresolved, and are not counted as passing.** They fail to
*collect* against their parent commit — the ADR added a symbol the test imports,
so the whole file errors and per-test discrimination cannot be read off it. That
is a genuine limit of this control, not a clean bill: `test_a_slice_is_not_the_suite.py`
was in that group, and the one hole ADR-066 found by hand was inside it. Closing
them needs the per-ADR mutation, which is judgement, not mechanism.

## Mechanism

No `src/` change, no version bump. v0.111.0 stays the build every banked run-19
checkpoint is keyed to (ADR-052 keys on `avs_version`, and a release invalidates
the lot mid-purchase).

The suite count does not move: five existing tests were strengthened, none added.

## References

- ADR-066 — the instance this generalises, and its own control section
- ADR-064 — the moved test seam: the other way a test stops watching its subject
- ADR-051 — one fact, one reader; the shape two of these five turned out to be
- ADR-054 — running the thing beats reading it
