"""A build failure must arrive as a fact, not as banner art (ADR-042).

Bench run 15 recorded a real build failure — case 01, task 团购详情查询,
three attempts, `1 failed, 27 passed` every time — as this:

    last failure: ==== FAILURES ==== ____ test_huge_id_no_crash ____
    server = ('127.0.0.1', 64131) def test_huge_id_no_crash(server):
    huge = "9" *

240 characters, cut off mid-expression, containing no assertion and no
verdict. The cause travelled with the sentence exactly as ADR-037
intended; the budget was spent on pytest's decorative rules before any
fact could reach it. The full output was preserved in `test_summary` all
along — the defect is in what gets condensed into `detail`, which is the
field the bench rows, the founder report and outcomes.yaml read.
"""

from __future__ import annotations

import pathlib
import re

from ai_venture_studio.testing import salient_failure

# Verbatim from run 15's result-2026-08-14-0702.yaml, task t2.
RUN_15_OUTPUT = """\
=================================== FAILURES ===================================
____________________________ test_huge_id_no_crash _____________________________

server = ('127.0.0.1', 64131)

    def test_huge_id_no_crash(server):
        huge = "9" * 40
        status, body = _get(server, "/api/groupbuys/%s" % huge)
>       assert status == 404
E       assert 400 == 404

tests/test_get_groupbuy_notfound.py:27: AssertionError
=========================== short test summary info ============================
FAILED tests/test_get_groupbuy_notfound.py::test_huge_id_no_crash - assert 40...
1 failed, 27 passed in 7.23s
"""


def test_the_run_15_failure_now_names_the_test():
    out = salient_failure(RUN_15_OUTPUT)
    assert "test_huge_id_no_crash" in out
    assert "tests/test_get_groupbuy_notfound.py" in out


def test_the_run_15_failure_now_carries_the_assertion():
    """pytest elides its own summary line to terminal width, so the `E` line
    is the only place the real comparison survives."""
    assert "assert 400 == 404" in salient_failure(RUN_15_OUTPUT)


def test_the_old_truncation_produced_neither():
    """The regression this exists to prevent, stated as the old behaviour."""
    old = " ".join(RUN_15_OUTPUT.split())[:240]
    assert "assert 400 == 404" not in old
    assert old.count("=") + old.count("_") > 100  # the budget, spent on rules


def test_no_banner_rules_survive():
    out = salient_failure(RUN_15_OUTPUT)
    assert "====" not in out and "____" not in out


def test_the_result_is_one_line():
    """It is interpolated into a sentence; a newline would break the row."""
    assert "\n" not in salient_failure(RUN_15_OUTPUT)


def test_it_fits_the_budget_it_is_given():
    assert len(salient_failure(RUN_15_OUTPUT, limit=60)) <= 62  # + " …"


def test_truncation_lands_on_a_word_boundary():
    """A cut mid-expression reads as a different expression — which is how
    `huge = "9" *` came to look like the failure."""
    clipped = salient_failure(RUN_15_OUTPUT, limit=40)
    assert clipped.endswith("…")
    assert not re.search(r"\w…$", clipped)


# --- runs with no short-summary section ---------------------------------


def test_a_collection_error_still_names_its_file():
    text = """\
==================================== ERRORS ====================================
_______________ ERROR collecting tests/test_groupbuys_read.py __________________
E   TypeError: make_server() takes 1 positional argument but 3 were given
=========================== short test summary info ============================
ERROR tests/test_groupbuys_read.py::test_nonexistent_id
"""
    out = salient_failure(text)
    assert "tests/test_groupbuys_read.py" in out
    assert "TypeError" in out


def test_assertions_carry_a_run_with_no_summary_section():
    text = """\
=================================== FAILURES ===================================
______________________________ test_totals _____________________________________
E       assert 17 == 18
"""
    assert salient_failure(text) == "E assert 17 == 18"


def test_output_with_neither_falls_back_to_its_own_text():
    assert salient_failure("boom: the runner died") == "boom: the runner died"


def test_a_passing_summary_passes_through_unharmed():
    """Every built task's `test_summary` goes through the same helper."""
    assert salient_failure("14 passed in 0.12s") == "14 passed in 0.12s"


def test_empty_input_is_empty_not_a_crash():
    assert salient_failure("") == ""
    assert salient_failure(None) == ""


def test_a_rules_only_input_yields_nothing_rather_than_rules():
    assert salient_failure("======\n______\n   \n") == ""


# --- every failure is reported, not just the first ----------------------


def test_all_failures_are_named_not_only_the_first():
    text = """\
=========================== short test summary info ============================
FAILED tests/test_a.py::test_one - assert 1 == 2
FAILED tests/test_b.py::test_two - KeyError: 'id'
2 failed, 5 passed in 1.02s
"""
    out = salient_failure(text, limit=400)
    assert "test_one" in out and "test_two" in out


def test_the_tally_line_is_not_mistaken_for_a_failure():
    text = """\
=========================== short test summary info ============================
FAILED tests/test_a.py::test_one - assert 1 == 2
2 failed, 5 passed in 1.02s
"""
    assert "5 passed" not in salient_failure(text)


# --- one definition ------------------------------------------------------


def test_no_helper_in_testing_py_is_defined_twice():
    """This module already owned a `_clip(text, head, tail)` for faulthandler
    dumps. Adding a second `def _clip` here rebound the name for the whole
    module, so the hang-dump path silently started calling the wrong helper
    with the wrong arity — five test_test_gate failures, no import error.
    A duplicate top-level name is never intentional in this file.
    """
    import ast

    from ai_venture_studio import testing

    tree = ast.parse(pathlib.Path(testing.__file__).read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"shadowed top-level definitions: {duplicates}"


def test_the_build_stage_does_not_keep_its_own_truncation():
    """The 240-char head-slice lived in build.py. A second copy here is how
    one of these drifts from the other (ADR-038)."""
    from ai_venture_studio.upstream import build

    code = "\n".join(
        line for line in pathlib.Path(build.__file__).read_text(
            encoding="utf-8"
        ).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "[:240]" not in code
    assert "salient_failure" in code
