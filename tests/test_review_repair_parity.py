"""A severity you block on is a severity you must try to repair (ADR-037).

The leader blocked a verdict on {CRITICAL, HIGH, MEDIUM}. The repair pass in
`review_and_repair` had its own hard-coded ("critical", "high"). So a task
whose worst finding was MEDIUM was marked REQUEST_CHANGES, no fix was ever
attempted on it, and nothing later in the run could clear it — unclean by
construction rather than by quality.

Medium is the modal severity the voters raise (89 of ~187 findings across
bench run 13's preserved workspaces, against 17 high and 2 critical), so this
was most of the unclean rows, not an edge: run 14 scored clean-review 38%
with build and probes both at 100%, and 7 of its 11 rejections were this
shape. The two thresholds were never pinned to each other by a test, which is
how they drifted apart unnoticed.

Second failure, same rows: `detail` was written ONLY when a fix iteration
ran, so a medium-only rejection reached the scoreboard with an empty reason —
11 of run 14's 17 outcomes. A reviewer rejecting everything correctly and one
rejecting everything spuriously produced byte-identical records.
"""
from __future__ import annotations

import inspect
import types

import pytest

from ai_venture_studio.leader import ACTIONABLE_SEVERITIES, synthesize
from ai_venture_studio.state import (
    Confidence,
    Severity,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)
from ai_venture_studio.upstream import autopilot

CLEAN = (Verdict.APPROVE.value, Verdict.APPROVE_WITH_NOTES.value)


def _finding(severity: Severity, title: str = "unchecked input") -> VoterFinding:
    return VoterFinding(
        voter="correctness", title=title, severity=severity,
        confidence=Confidence.CERTAIN, file_path="app/main.py",
        line_start=10, line_end=10, evidence="x = req.json()['id']",
        explanation="no validation", verification="confirmed", score=90,
    )


def _review(verdict: str, findings: list[VoterFinding]):
    """The shape `_review_head` returns, minus the model call."""
    return types.SimpleNamespace(
        verdict=types.SimpleNamespace(value=verdict), findings=findings
    )


def _run(monkeypatch, tmp_path, review, after=None, landed=False):
    """review_and_repair with the review and the repair both stubbed.

    task_id is left empty so no progress file is required.
    """
    calls: list[list[VoterFinding]] = []
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)

    def _fake_fix(root, provider, model, findings):
        calls.append(list(findings))
        return landed, after

    monkeypatch.setattr(autopilot, "_fix_iteration", _fake_fix)
    verdict, detail, approvals = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="task-1",
    )
    return verdict, detail, calls


@pytest.mark.parametrize("severity", sorted(ACTIONABLE_SEVERITIES, key=str))
def test_every_severity_that_blocks_a_verdict_is_one_the_repair_pass_attempts(
    severity, monkeypatch, tmp_path
):
    """The invariant that was missing. Blocking on a severity the fix loop
    ignores is a dead end: the task can never reach clean."""
    blocking = synthesize([
        VoterOutput(voter="correctness", model="m", status=VoterStatus.OK,
                    findings=[_finding(severity)])
    ])
    assert blocking.verdict.value not in CLEAN, (
        f"{severity} is in ACTIONABLE_SEVERITIES but does not block — "
        "the set no longer means what the repair pass reads it as"
    )
    _, _, calls = _run(
        monkeypatch, tmp_path, _review("REQUEST_CHANGES", [_finding(severity)])
    )
    assert calls, f"{severity} blocks the verdict but no repair was attempted"
    assert calls[0][0].severity is severity


def test_a_medium_only_rejection_gets_a_repair_attempt(monkeypatch, tmp_path):
    """The exact run-14 shape: nothing critical or high, and previously no
    fix ever ran."""
    _, _, calls = _run(
        monkeypatch, tmp_path,
        _review("REQUEST_CHANGES", [_finding(Severity.MEDIUM)]),
    )
    assert len(calls) == 1 and calls[0][0].severity is Severity.MEDIUM


def test_a_rejection_records_what_it_objected_to(monkeypatch, tmp_path):
    """An empty reason beside a REQUEST_CHANGES is the evidence-deletion bug
    one stage over from ADR-036."""
    _, detail, _ = _run(
        monkeypatch, tmp_path,
        _review("REQUEST_CHANGES", [
            _finding(Severity.MEDIUM, "missing WHERE clause"),
            _finding(Severity.MEDIUM, "unbounded query"),
        ]),
    )
    assert "2 medium" in detail
    assert "missing WHERE clause" in detail


def test_a_clean_verdict_does_not_carry_a_findings_dump(monkeypatch, tmp_path):
    """APPROVE_WITH_NOTES is clean; its low-severity notes must not turn the
    scoreboard row into a review transcript."""
    _, detail, calls = _run(
        monkeypatch, tmp_path,
        _review("APPROVE_WITH_NOTES", [_finding(Severity.LOW, "naming nit")]),
    )
    assert "naming nit" not in detail
    assert not calls, "a low-only review must not spend a repair round-trip"


def test_the_reason_describes_the_review_that_produced_the_verdict(
    monkeypatch, tmp_path
):
    """After a fix iteration the verdict comes from the RE-review, so the
    reasons must too — otherwise the row names findings just repaired."""
    before = _review("REQUEST_CHANGES", [_finding(Severity.HIGH, "sql injection")])
    after = _review("REQUEST_CHANGES", [_finding(Severity.MEDIUM, "still unbounded")])
    _, detail, calls = _run(monkeypatch, tmp_path, before, after=after, landed=True)
    assert calls, "a high finding must still be repaired"
    assert "still unbounded" in detail
    assert "sql injection" not in detail, "reported a finding that was fixed"


def test_the_repair_set_is_not_re_listed_in_autopilot():
    """The drift itself: two literal lists of severities in two files."""
    source = inspect.getsource(autopilot.review_and_repair)
    assert "ACTIONABLE_SEVERITIES" in source
    # Comments only — the comment above the fix loop QUOTES the old pair to
    # explain the drift, so matching raw source would fail on its own
    # documentation (the same trap as asserting "PORT" against a message
    # whose heading reads "IMPORT GATE").
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert '"critical", "high"' not in code, (
        "the hard-coded pair is what diverged from the leader's set"
    )


def test_the_summary_is_bounded():
    """It rides in every bench row; it must not become a review dump."""
    many = [_finding(Severity.MEDIUM, f"finding number {i}") for i in range(12)]
    out = autopilot._findings_summary(many)
    assert len(out) <= 240
    assert "12 medium" in out
    assert "+9 more" in out


def test_no_findings_means_no_note():
    assert autopilot._findings_summary([]) == ""
