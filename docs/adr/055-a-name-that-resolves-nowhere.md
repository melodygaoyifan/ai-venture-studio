# ADR-055 — a name that resolves nowhere

**Status:** accepted (2026-08-20)

**Answers:** "fix all issues" — asked after ADR-054, which had just
demonstrated that the previous answer to this question was found by running
the code rather than by reading it.

**Reverses:** nothing. Extends ADR-054 from one instance to its class.

## Context

ADR-054 fixed `avs bench-criterion`, which had shipped ten orphaned lines
calling a `streak_state` that existed nowhere in the codebase. It crashed on
every healthy run, through eleven recorded benchmarks, and nothing noticed.

That record's closing lesson was that running the command found in one
invocation what an audit could not. True, and incomplete. The more useful
observation is *why* neither the audit nor the suite could have found it:

> A test proves that the code it calls works. It says nothing whatsoever
> about code that no test calls.

The orphaned block was unreachable from every test in the suite. No coverage
target reaches it, because coverage measures the code you ran. This project
has 2229 hermetic tests and a CI comment asserting that **the suite IS the
gate** — and the suite is structurally incapable of catching this defect
class, because the class is defined by not being on any executed path.

`ruff check` reads every line whether or not anything runs it. That is not a
better test; it is a different kind of instrument, and the project had none.

### What the instrument found

87 findings under the pyflakes rule set. Two were the identical defect:

**1. A security boundary typed against a class nobody imported.**
`MCPHost.__init__` annotated `taint: "TaintGuard | None"`, and no import of
`TaintGuard` existed in the module. Under `from __future__ import
annotations` the annotation is a string that is never evaluated, so it never
raised — it simply meant the declared type on the risk-tier RBAC boundary
(doc 11 §17.3, the triple check) was verified by nothing, and
`typing.get_type_hints()` on it raised `NameError`. Confirmed against the
deployed v0.103.0 before the fix.

**2. A guard whose failure message could not be printed.**
`test_every_stage_command_enforces_its_floor` formats `{floor.name}` into
its assertion message; `floor` is not in scope. That test guards eight
stages against running below their infrastructure floor — the gap ADR-U15
was written for. The message is constructed *only when the assertion fails*,
so on the day it finally caught a regression it would have died with
`NameError` instead of naming which stage ran where.

Both are ADR-054's shape exactly: code on a path nothing exercises. Neither
was reachable by any test, and both took 300ms to find.

The remaining 85 were mechanical — dead imports, vestigial `f` prefixes, four
unused locals. Worth clearing, but they are not why this record exists; they
are the reason the two that mattered had nowhere to stand out.

## Decision

1. **Add `ruff check src/ tests/` to CI, selecting `F` only.** Pyflakes
   rules describe code that *cannot work*, not code someone would format
   differently. F821 (undefined name) is the rule this gate exists for.

2. **No style linting.** Selecting `E`/`W`/`I` would reformat several
   hundred files and produce hundreds of findings, which is how a gate
   becomes something people learn to scroll past. `test_the_gate_is_scoped_
   to_rules_about_code_that_cannot_work` pins `select = ["F"]`, so a later
   widening has to be deliberate rather than incidental.

3. **Both workflows run it, and a test says so.** `publish.yml` states in a
   comment that it runs "the same gate as ci.yml" and then assembles it by
   hand a second time. Prose is not a mechanism — that is one control with
   two call paths (ADR-051), and the release path is the worst place to
   discover which of the two does less. `test_both_workflows_run_the_gate`
   reads both files.

4. **The suite runs it too, skipping when ruff is absent** — the same
   `shutil.which` pattern the git-dependent suites already use. A gate that
   lives only in CI teaches the author to find out from a red workflow after
   the push.

5. **`F401` is ignored in `__init__.py`.** A package `__init__` exists to
   re-export. `lanes/__init__.py` computes `__all__` from `dir()`, which no
   linter can follow, and its 47 "unused" imports are the lanes' public
   surface — deleting them would have broken every `from
   ai_venture_studio.lanes import ...` in the tree.

6. **`TaintGuard` is imported at runtime, not under `TYPE_CHECKING`.** The
   conventional fix for an annotation-only name is the deferred import, and
   here it would have been wrong: with `from __future__ import annotations`
   already in force, `TYPE_CHECKING` satisfies the linter and leaves
   `get_type_hints()` raising the identical `NameError`. That is the defect
   preserved and the report silenced. `taint_guard` imports nothing but
   stdlib, so there is no cycle to avoid and no cost to paying for it.

## What stays out

**Coverage measurement.** The tempting response to "no test reaches this
line" is a coverage gate, and it answers the wrong question: coverage tells
you which lines ran, and would have scored the orphaned block as uncovered
alongside several hundred legitimately-untested branches. The linter says
the line *cannot* work, which is a claim worth failing a build over.

**Type checking.** mypy or pyright would also have caught defect 1, and
would produce thousands of findings on a codebase this size that has never
had them. That is a real project, deliberately not this one, and it is
noted here so the next person does not mistake this record for having
settled the question.

**Any rewriting of the 85 mechanical findings into a narrative.** They were
dead imports. They are gone.

## What keeps this honest

`tests/test_a_name_that_resolves_nowhere.py`, 5 tests.

**Control:** `typing.get_type_hints(MCPHost.__init__)` raises `NameError:
name 'TaintGuard' is not defined` against the deployed v0.103.0. Run before
the fix, output recorded above.

**Not claimed:** that the two undefined names ever caused an incident. They
did not, and neither did `streak_state` until it did. What is claimed is
narrower and checkable — that all three were invisible to 2229 tests and a
careful reading, and visible to a 300ms mechanical check that this project
did not have until now.

## The lesson worth keeping

ADR-054 said running the command beat reading the code. The generalisation
is that **both are sampling instruments, and they sample the same thing** —
the paths something actually takes. Three defects in two records now have
sat on paths nothing took.

The suite is still the gate for whether the system works. It was never a
gate for whether the code means anything, and the CI comment that claimed
otherwise has been corrected rather than deleted, because the claim was
specific and specifically wrong.
