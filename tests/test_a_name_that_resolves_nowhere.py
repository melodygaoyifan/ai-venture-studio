"""A name that resolves nowhere (ADR-055).

ADR-054 found `avs bench-criterion` calling a `streak_state` that existed
nowhere in the codebase. It had shipped through eleven recorded benchmark
runs and crashed on every healthy invocation, and what finally found it was
running the command — after an audit that read the module had not.

The audit could not have found it and neither could the suite, for the same
reason: **a test proves the code it calls works, and says nothing about code
no test calls.** The orphaned block was unreachable from any test, so no
amount of coverage would have reached it.

`ruff check` reads every line whether or not anything runs it. Wiring it in
found two more of the identical defect — a name in an annotation and a name
in an assertion message, both of which resolve nowhere and neither of which
any test could reach:

1. `MCPHost.__init__` annotated `taint` as `TaintGuard | None` and nothing
   imported `TaintGuard`. Lazy under `from __future__ import annotations`,
   so it never raised — it just meant the type on a risk-tier RBAC boundary
   was checked by no one.

2. `test_every_stage_command_enforces_its_floor` formats `{floor.name}` into
   its failure message, and `floor` is not in scope. That test guards eight
   stages against running below their infrastructure floor; the day it
   caught a regression it would have died with `NameError` instead of
   naming which stage ran where.

Both are the ADR-054 shape exactly: code on a path nothing exercises, which
is why the gate has to be one that does not need to exercise anything.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import typing

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# The two names now resolve
# ---------------------------------------------------------------------------


def test_the_taint_annotation_names_a_class_that_exists():
    """`get_type_hints` is the check, not "does it import" — a TYPE_CHECKING
    import would have satisfied the linter and left this raising the same
    NameError, silencing the report while keeping the defect."""
    from ai_venture_studio.harness.taint_guard import TaintGuard
    from ai_venture_studio.mcp.host import MCPHost

    hints = typing.get_type_hints(MCPHost.__init__)
    assert hints["taint"] == typing.Optional[TaintGuard]


def test_the_floor_diagnostic_can_actually_be_printed():
    """The message is built only when the assertion FAILS, so a broken name
    in it costs nothing until the exact moment it is needed most: a real
    regression in a stage-gating guard, reported as a NameError."""
    from ai_venture_studio.adoption.substrate import STAGE_FLOORS, Rung

    # Every combination the parametrization can reach, not one sample: the
    # first draft of this test rendered `build`, which is the stage's ARGV
    # and not its key, and traded the NameError for a KeyError — the same
    # defect wearing a different exception.
    for stage in STAGE_FLOORS:
        for below in Rung:
            rendered = (
                f"{stage} ran at {below.name} despite a floor of "
                f"{STAGE_FLOORS[stage].name}: "
            )
            assert f"{stage} ran at {below.name}" in rendered

    assert "coding" in STAGE_FLOORS, "the `build` command's stage key"


# ---------------------------------------------------------------------------
# The gate that catches the class, and catches it in both places
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
def test_no_name_in_the_tree_resolves_nowhere():
    """The suite's own copy of the CI gate. Skipped rather than vendored when
    ruff is absent, the same way the git-dependent suites already skip — but
    present here so `pytest` alone catches this class locally, instead of the
    author learning about it from a red workflow after the push."""
    proc = subprocess.run(
        [shutil.which("ruff"), "check", "--no-cache", "src/", "tests/"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_both_workflows_run_the_gate():
    """`publish.yml` says in a comment that it runs "the same gate as ci.yml"
    and then assembles it by hand a second time. Prose is not a mechanism:
    that is one control with two call paths (ADR-051), and a release is the
    worst place to find out the two had drifted."""
    workflows = REPO / ".github" / "workflows"
    for name in ("ci.yml", "publish.yml"):
        body = (workflows / name).read_text(encoding="utf-8")
        assert "ruff check src/ tests/" in body, (
            f"{name} does not run the lint gate — the two workflows have "
            f"drifted, which is the thing this test exists to prevent"
        )


def test_the_gate_is_scoped_to_rules_about_code_that_cannot_work():
    """Scope is load-bearing. Selecting style rules here would bury F821 in
    hundreds of formatting findings, and a gate people learn to scroll past
    is not a gate.

    This test used to read `select == ["F"]`, and said of itself: "if a later
    change widens `select`, this is the test that asks whether the widening
    was deliberate." ADR-062 widened it and answers here. The answer is yes,
    and the scope rule survives intact — every family added is about code
    that misbehaves (`B`, `S`, `BLE`, `RET`, `PIE`, `A`, `LOG`, `G`, `PTH`,
    `C4`, `RSE`, `TID`), the widening found three live defects on its first
    run, and it left the codebase at zero findings rather than at a backlog
    people scroll past.

    What this now pins is the boundary, not the number: the cosmetic families
    stay out. `E`/`W` line-length and whitespace, `D` docstring formatting,
    `Q` quote style and `ANN` annotations are the ones that would bury F821,
    and they are the reason the original narrowness was right.
    """
    import tomllib

    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    select = cfg["tool"]["ruff"]["lint"]["select"]
    assert "F" in select, "the family the gate was built for"
    for cosmetic in ("E", "D", "Q", "ANN", "COM", "EM", "FBT", "ERA"):
        assert cosmetic not in select, (
            f"{cosmetic} is about how code is written, not whether it works. "
            f"Selecting it buries F821 in findings nobody reads, which is the "
            f"failure ADR-055 drew this boundary to avoid."
        )
    # `W` is in, and it is the exception that has to be argued rather than
    # assumed: the selected `W` rules here are trailing-whitespace and
    # invalid-escape-sequence, and the second is a real defect (`"\d"` is a
    # deprecation today and a different string tomorrow). It stays because it
    # is currently at zero, not because whitespace matters.
    assert "W605" not in cfg["tool"]["ruff"]["lint"].get("ignore", [])
