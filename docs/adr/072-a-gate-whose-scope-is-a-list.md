# ADR-072 — a gate whose scope is a list

**Status:** accepted (2026-08-22) · **Release**: unreleased (tooling and tests only)

## Context

ADR-055 wired `ruff check` into CI on an argument about what tests cannot do:

> A test proves the code it calls works, and says nothing about code no test
> calls. […] `ruff check` reads every line whether or not anything runs it.

It was wired in as `ruff check src/ tests/`, and stayed that way through
seventeen ADRs. `scripts/` was never in it.

That is the one directory the argument is squarely about. Everything in
`scripts/` is by construction code no test calls — `gate-probe.py`,
`retag.sh`'s Python siblings, the demo builders — so the gate that exists
because the suite cannot reach unreached code was not reading the unreached
code.

It surfaced while closing ADR-071, from a `ruff check .` run typed by accident
instead of the enumerated form. ADR-071 recorded it as measured debt and
declined it, because widening a gate is a scope decision and not something to
fold into an unrelated change:

> Newly measured, not fixed: `scripts/` sits outside the linted surface […]
> and carries 3 errors — 2× `S108`, 1× `B905`.

This reverses that decline.

## Decision

**The gate reads `.`, not a list of directories.**

`ci.yml`, `publish.yml` and the suite's own copy of the gate
(`test_no_name_in_the_tree_resolves_nowhere`) all run `ruff check .`. Nothing
that gets added to the repository is outside the gate by default, and no one
has to remember to extend it.

The enumerated form is now asserted *against*:
`test_the_gate_reads_the_whole_tree_and_not_a_list_of_directories` fails if
either workflow narrows back to `src/ tests/`.

### What was actually there

Modest, and that is the finding rather than a disappointment:

| | |
|---|---|
| `S108` ×2 | `scripts/demo/build_demo.py`, `build_voiceover.py` default their work directory to `/tmp/avs-demo` |
| `B905` ×1 | `scripts/gate-probe.py` zips a case's requests against its expectations without `strict=` |

**The `/tmp` default is a real if small hardening.** The two scripts share it —
one writes the frames the other reads — so they must agree on a deterministic
path and neither can use a `mkdtemp`. What can be removed is the
*world-writable* part, not the predictability, so the default moves to
`~/.cache/avs-demo`. Two copies of one constant is ADR-051's shape, and the
failure mode is silent (the second script finds no frames and reports an empty
run), so `test_the_two_demo_scripts_agree_on_where_the_work_lives` pins them
together.

**The `zip()` was not a live defect, and the record says so.** `main` already
refuses a case whose requests and expectations differ in length. `strict=True`
is defence in depth — the guard is forty lines away and the loop is where
truncation would actually cost something, a rate printed over a denominator
nobody chose (ADR-035). Two layers, each silent about the other's case, which
is the arrangement ADR-056 already used for simulated results.

Naming this accurately matters more than the fix. The first version of this
change described the `zip()` as truncating a probe's denominator, which would
have been a good story and was false — the guard above it was found only on
re-reading. **The point is not that `scripts/` was dangerous. It is that
nobody knew either way, because nothing read the files.**

### Control

A file planted in `scripts/` with a bare `zip()` fails
`test_no_name_in_the_tree_resolves_nowhere` naming `B905`; removed, it passes.
The widening is real in both directions rather than a string change in a
workflow.

PC-1 2485 → **2487**. No version bump: nothing under `src/` changed.

## What stays out

**Widening `select`.** ADR-062 set the rule families and
`test_the_gate_is_scoped_to_rules_about_code_that_cannot_work` pins the
boundary — every family is about code that misbehaves, none is cosmetic. This
ADR changes *which files* are read, not *which rules* are applied, and mixing
the two would make the next reader unable to tell which decision produced a
finding.

**`ruff format`.** Still out, for ADR-062's reason: a gate people learn to
scroll past is not a gate.
