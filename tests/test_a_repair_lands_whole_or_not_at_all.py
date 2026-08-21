"""ADR-044 — the repair pass must land whole, or land nothing and say why.

Bench run 16's clean-review rate fell 55% → 31%, and six of its eleven
rejections carried one sentence: *"a fix was attempted and rolled back — it
did not clear the review"*. The sentence was usually false. Five different
outcomes produced it, only one of which involves a review, and the most
common one was this:

    reviewer   MEDIUM — "test boilerplate duplicated verbatim across six new
               test files instead of a shared fixture/helper"
    repair     writes tests/helpers.py plus six rewritten call sites
    _write_files  drops all six call sites (the asserts they moved into the
               helper read as `removed_assert`) and keeps the helper
    suite      still passes — the untouched originals still assert
    commit     the half-change goes in
    re-review  the duplication is still there, and now there is an orphan
               helper nothing calls: *"Unused alias function diverges from
               spec's stated call path"* (run 16, 03-t5)

The repair pass was manufacturing the findings that rejected it, and the
scoreboard charged them to the product.

These tests pin the rule, not the instance.
"""
from __future__ import annotations

import ast
import inspect
import subprocess

import pytest

from ai_venture_studio.state import (
    Confidence, LeaderResult, Severity, Verdict, VoterFinding,
)
from ai_venture_studio.tools.integrity import assertion_delta
from ai_venture_studio.upstream import autopilot
from ai_venture_studio.upstream.build import _write_files
from ai_venture_studio.executables import resolve


# --- the wall tells a move from a deletion ----------------------------------

_DUPLICATED = (
    "def _server():\n"
    "    return object()\n"
    "def test_get():\n"
    "    resp = _server()\n"
    "    assert resp is not None\n"
    "    assert isinstance(resp, object)\n"
)
_HOISTED_HELPER = (
    "def server():\n"
    "    return object()\n\n"
    "def assert_ok(resp):\n"
    "    assert resp is not None\n"
    "    assert isinstance(resp, object)\n"
)
_CALL_SITE = "from tests.helpers import server, assert_ok\n\ndef test_get():\n    assert_ok(server())\n"


def test_an_assert_moved_into_a_helper_in_the_same_batch_is_not_weakening():
    assert assertion_delta(_DUPLICATED, _CALL_SITE, elsewhere=[_HOISTED_HELPER]) == []


def test_an_assert_that_lands_nowhere_in_the_batch_is_still_a_removal():
    """The reward-hacking defence is the point of this guard and survives."""
    gutted = "def test_get():\n    resp = _server()\n    assert resp is not None\n"
    changes = assertion_delta(_DUPLICATED, gutted, elsewhere=[_HOISTED_HELPER.replace(
        "assert isinstance(resp, object)", "pass")])
    assert [c.change for c in changes] == ["removed_assert"]
    assert "isinstance" in changes[0].node


def test_a_skip_cannot_be_moved_into_existence():
    """`added_skip` is never forgiven by relocation — there is nowhere for a
    skip to have moved *from*."""
    after = "import pytest\n\n@pytest.mark.skip\ndef test_get():\n    pass\n"
    changes = assertion_delta(_DUPLICATED, after, elsewhere=[_HOISTED_HELPER])
    assert any(c.change == "added_skip" for c in changes)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "p"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_api1.py").write_text(_DUPLICATED, encoding="utf-8")
    (root / "tests" / "test_api2.py").write_text(_DUPLICATED, encoding="utf-8")
    subprocess.run([resolve("git"), "init", "-q"], cwd=root, check=True)
    return root


def test_the_consolidation_refactor_the_reviewer_asks_for_can_be_performed(workspace):
    """End to end through the real writer: the exact repair run 16 kept
    rejecting now reaches disk whole."""
    written, kept = _write_files(workspace, [
        {"path": "tests/helpers.py", "new_content": _HOISTED_HELPER},
        {"path": "tests/test_api1.py", "new_content": _CALL_SITE},
        {"path": "tests/test_api2.py", "new_content": _CALL_SITE},
    ])
    assert kept == [], "the fix was dropped by the write-guard"
    assert set(written) == {"tests/helpers.py", "tests/test_api1.py", "tests/test_api2.py"}


# --- a partial application is not a repair ----------------------------------

def _finding(sev=Severity.MEDIUM):
    return VoterFinding(
        voter="quality", title="duplicated test boilerplate", severity=sev,
        confidence=Confidence.CERTAIN, file_path="tests/test_api1.py",
        line_start=1, line_end=1, evidence="-", explanation="hoist it",
    )


def _repair_returning(monkeypatch, *, written, kept, review_after=None):
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream import build as build_mod

    monkeypatch.setattr(
        autopilot, "get_provider",
        lambda name: type("P", (), {
            "complete": staticmethod(lambda **k: "files: []")})(),
    )
    monkeypatch.setattr(autopilot, "last_response_truncated", lambda: False)
    monkeypatch.setattr(
        build_mod, "_write_files", lambda root, f, **kw: (list(written), list(kept))
    )
    monkeypatch.setattr(
        testing_mod, "_pytest_in_subprocess",
        lambda root: testing_mod.TestReport(status="no_tests", summary="none"),
    )
    monkeypatch.setattr(testing_mod, "run_js_tests", lambda root: None)
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: review_after or LeaderResult(
            verdict=Verdict.APPROVE, summary="clean"),
    )


def test_a_repair_the_write_guard_partly_refused_is_applied_to_none(
    workspace, monkeypatch
):
    """The half-application that produced the orphan helper.

    A batch whose helper landed and whose call sites were dropped must leave
    the tree as it found it, not commit the half.
    """
    subprocess.run([resolve("git"), "add", "-A"], cwd=workspace, check=True)
    subprocess.run([resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "feat"], cwd=workspace, check=True)
    (workspace / "tests" / "helpers.py").write_text(_HOISTED_HELPER, encoding="utf-8")
    _repair_returning(
        monkeypatch,
        written=["tests/helpers.py"],
        kept=["tests/test_api1.py (skeleton kept — your version dropped: "
              "removed_assert: assert resp is not None)"],
    )

    landed, after, why = autopilot._fix_iteration(
        workspace, "mock", "m", [_finding()]
    )

    assert landed is False
    assert after is None, "a refused repair was never reviewed"
    assert "refused by the write-guard" in why
    assert "test_api1.py" in why, "the row must name the file that was dropped"
    log = subprocess.run([resolve("git"), "log", "--oneline"], cwd=workspace,
                         capture_output=True, text=True).stdout
    assert "address serious review findings" not in log, (
        "a half-applied repair was committed"
    )
    assert not (workspace / "tests" / "helpers.py").exists(), (
        "the orphan helper survived the refusal"
    )


# --- five failures, five reasons --------------------------------------------

def test_every_way_the_repair_can_fail_names_which_one():
    """An AST walk, so a sixth failure path cannot be added silently.

    Six paths returned `False` and the caller printed one sentence for all of
    them — the sentence that names a review, when four of the six never
    reached one.
    """
    tree = ast.parse(inspect.getsource(autopilot._fix_iteration).lstrip())
    bare: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        first = node.value.elts[0]
        if not (isinstance(first, ast.Constant) and first.value is False):
            continue
        reason = node.value.elts[2] if len(node.value.elts) > 2 else None
        empty = reason is None or (
            isinstance(reason, ast.Constant) and not str(reason.value).strip()
        )
        if empty:
            bare.append(node.lineno)
    assert not bare, (
        f"_fix_iteration returns False without a reason at line(s) {bare} — "
        "that is how six distinct outcomes came to read as one"
    )


def test_the_repair_pass_refuses_a_truncated_response(workspace, monkeypatch):
    """Every other writer stage checks this (ADR-041); this was the one that
    never did, and it is the stage that must return complete file bodies."""
    _repair_returning(monkeypatch, written=["x"], kept=[])
    monkeypatch.setattr(autopilot, "last_response_truncated", lambda: True)

    landed, after, why = autopilot._fix_iteration(
        workspace, "mock", "m", [_finding()]
    )
    assert landed is False and after is None
    assert "cut off" in why and str(autopilot._FIX_MAX_TOKENS) in why


# --- the verdict describes the code that survived ---------------------------

def _review_and_repair(monkeypatch, tmp_path, *, first, landed, after, why):
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: first)
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (landed, after, why)
    )
    return autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t",
    )


def test_a_discarded_repair_does_not_get_to_set_the_verdict(monkeypatch, tmp_path):
    """Run 16's `04-t3`.

    Recorded ESCALATE_SECURITY_RISK for *"input validation removed for
    non-integer candidate IDs"* — the repair removed it, the rollback put it
    back, and the guard was in the delivered code the whole time the
    scoreboard said it was gone. A `git reset --hard` restores the tree; the
    verdict has to be restored with it.
    """
    original = LeaderResult(
        verdict=Verdict.REQUEST_CHANGES, summary="notes",
        findings=[_finding(Severity.MEDIUM)],
    )
    of_the_discarded_diff = LeaderResult(
        verdict=Verdict.ESCALATE_SECURITY_RISK, summary="validation removed",
        findings=[VoterFinding(
            voter="security", title="input validation removed",
            severity=Severity.CRITICAL, confidence=Confidence.CERTAIN,
            file_path="app.py", line_start=1, line_end=1, evidence="-",
            explanation="the repair deleted the guard",
        )],
    )

    verdict, detail, _approvals, _by_voter = _review_and_repair(
        monkeypatch, tmp_path, first=original, landed=False,
        after=of_the_discarded_diff,
        why="the repair was reviewed, found critical findings of its own, "
            "and was discarded",
    )

    assert verdict == Verdict.REQUEST_CHANGES.value, (
        "the verdict came from a diff that was thrown away"
    )
    assert "input validation removed" not in detail, (
        "the row names a finding about code that does not exist"
    )
    assert "duplicated test boilerplate" in detail, (
        "the row must name what is actually wrong with the delivered code"
    )
    assert "discarded" in detail, "the discarded attempt is still evidence"


def test_a_repair_that_landed_still_sets_the_verdict(monkeypatch, tmp_path):
    """The other direction, which ADR-037 established and this must not undo:
    when the fix stands, its re-review is the review of the shipped code."""
    verdict, detail, approvals, _ = _review_and_repair(
        monkeypatch, tmp_path,
        first=LeaderResult(verdict=Verdict.REQUEST_CHANGES, summary="s",
                           findings=[_finding(Severity.HIGH)]),
        landed=True,
        after=LeaderResult(verdict=Verdict.APPROVE, summary="clean"),
        why="",
    )
    assert verdict == Verdict.APPROVE.value
    assert "after fix iteration" in detail
    assert approvals and "repaired" in approvals[0]


def test_the_reason_reaches_the_row_instead_of_the_old_sentence(monkeypatch, tmp_path):
    _verdict, detail, _a, _b = _review_and_repair(
        monkeypatch, tmp_path,
        first=LeaderResult(verdict=Verdict.REQUEST_CHANGES, summary="s",
                           findings=[_finding(Severity.MEDIUM)]),
        landed=False, after=None,
        why="the repair wrote no files",
    )
    assert "the repair wrote no files" in detail
    assert "did not clear the review" not in detail, (
        "the sentence that named a review four of six failures never reached"
    )


# --- the run that rejects work keeps the evidence ---------------------------

def test_a_case_with_a_rejected_task_keeps_its_workspace():
    """Every one of run 16's six rolled-back tasks lived in a case that
    completed with all probes passing, so not one review survived to be read.
    The clean rate is the number this bench most often has to explain, and it
    was the only one whose evidence was deleted by design (ADR-036's family).
    """
    from ai_venture_studio import product_bench

    source = inspect.getsource(product_bench.run_case)
    # Split on the CALL, not on its argument list — the arguments grew a
    # `run_stamp` in v0.107.0 and this guard silently stopped matching, which
    # is the shape of failure it exists to prevent one level up.
    preserve = source.split("_preserve_workspace(")[0]
    condition = preserve.rsplit("if ", 1)[-1]
    assert "len(clean) < len(built)" in condition, (
        "a case whose work was rejected still throws away the reviews that "
        "say why"
    )
