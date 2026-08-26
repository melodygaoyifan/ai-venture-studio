"""A discarded repair informs its successor (ADR-081).

Run 19b, case 04, final run: task t8's review found a critical —
"New 405 guard makes POST /api/candidates unreachable" — the repair broke
the suite and was correctly discarded, and that ended the matter. The 405
guard shipped, and all three probes died on `AssertionError: (405, {})`.
The discard reason (the exact feedback a second attempt needed) had just
been composed — for the scoreboard, shown to no model. Same shape as
ADR-080's parse nudges: died with the cure in hand.

These pin the new contract: up to `MAX_REPAIR_ATTEMPTS` passes, each retry
handed why the previous one was discarded; the discarded diff's own
findings travel in the retry prompt only, never into the row.
"""
from __future__ import annotations

from ai_venture_studio.state import (
    Confidence, LeaderResult, Severity, Verdict, VoterFinding,
)
from ai_venture_studio.upstream import autopilot


def _finding(sev=Severity.HIGH, title="405 guard blocks POST /api/candidates"):
    return VoterFinding(
        voter="correctness", title=title, severity=sev,
        confidence=Confidence.CERTAIN, file_path="app/handlers.py",
        line_start=1, line_end=1, evidence="-", explanation="remove the guard",
    )


_REVIEW = LeaderResult(
    verdict=Verdict.REQUEST_CHANGES, summary="s", findings=[_finding()],
)


def _spy(monkeypatch, outcomes):
    """Replace _fix_iteration with a script; record each prior_failure."""
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: _REVIEW)
    calls: list[str] = []

    def fake(root, provider, model, findings, *, prior_failure=""):
        calls.append(prior_failure)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(autopilot, "_fix_iteration", fake)
    return calls


def test_the_second_attempt_hears_why_the_first_was_discarded(
    monkeypatch, tmp_path
):
    calls = _spy(monkeypatch, [
        (False, None, "the repair broke the suite (failed) and was discarded"),
        (True, LeaderResult(verdict=Verdict.APPROVE, summary="clean"), ""),
    ])

    verdict, detail, approvals, _bv, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t8",
    )

    assert calls[0] == "", "the first attempt has no history to hear"
    assert "broke the suite" in calls[1], (
        "the retry was not told why its predecessor was discarded — "
        "that is run 19b's bug"
    )
    assert verdict == Verdict.APPROVE.value
    assert "after fix iteration" in detail
    assert "an earlier repair attempt was discarded" in detail, (
        "the row must say the landing took two tries"
    )


def test_the_discarded_diffs_findings_ride_in_the_prompt_not_the_row(
    monkeypatch, tmp_path
):
    """The re-review of a discarded diff describes code that no longer
    exists. Its findings are exactly what the retry needs to hear — and
    exactly what the row must not print (the ADR-044 rule)."""
    of_the_discarded_diff = LeaderResult(
        verdict=Verdict.ESCALATE_SECURITY_RISK, summary="worse",
        findings=[_finding(
            Severity.CRITICAL, "input validation removed by the repair",
        )],
    )
    calls = _spy(monkeypatch, [
        (False, of_the_discarded_diff,
         "the repair was reviewed, found critical findings of its own, "
         "and was discarded"),
        (False, None, "the repair wrote no files"),
    ])

    verdict, detail, _a, _bv, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t8",
    )

    assert "input validation removed by the repair" in calls[1], (
        "the retry must hear WHAT the discarded attempt broke, not just "
        "that it was discarded"
    )
    assert "input validation removed by the repair" not in detail, (
        "the row names a finding about code that does not exist"
    )
    assert verdict == Verdict.REQUEST_CHANGES.value, (
        "the verdict describes the code that survived"
    )


def test_the_attempt_budget_is_bounded_and_the_row_names_each_failure(
    monkeypatch, tmp_path
):
    calls = _spy(monkeypatch, [
        (False, None, "the repair wrote no files"),
        (False, None, "the repair broke the suite (failed) and was discarded"),
        (False, None, "never reached"),
    ])

    verdict, detail, _a, _bv, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t8",
    )

    assert len(calls) == autopilot.MAX_REPAIR_ATTEMPTS
    assert verdict == Verdict.REQUEST_CHANGES.value
    assert "repair attempted, not applied" in detail
    assert "the repair wrote no files" in detail
    assert "broke the suite" in detail, (
        "two attempts failed two different ways; the row reports one"
    )


def test_a_landed_first_attempt_buys_no_second(monkeypatch, tmp_path):
    calls = _spy(monkeypatch, [
        (True, LeaderResult(verdict=Verdict.APPROVE, summary="clean"), ""),
        (False, None, "never reached"),
    ])

    verdict, detail, _a, _bv, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t8",
    )

    assert len(calls) == 1, "a landed repair must not be re-billed"
    assert verdict == Verdict.APPROVE.value
    assert "discarded" not in detail


def test_the_prior_failure_reaches_the_implementers_prompt(
    monkeypatch, tmp_path
):
    """Below the loop: _fix_iteration itself must show the reason to the
    model, in a block the model can distinguish from the findings."""
    seen: dict[str, str] = {}

    class _P:
        @staticmethod
        def complete(**kwargs):
            seen.update(kwargs)
            return "no yaml at all"

    monkeypatch.setattr(autopilot, "get_provider", lambda name: _P())
    monkeypatch.setattr(autopilot, "last_response_truncated", lambda: False)

    landed, _after, why = autopilot._fix_iteration(
        tmp_path, "mock", "m", [_finding()],
        prior_failure="the repair broke the suite (failed) and was discarded",
    )

    assert landed is False and "did not parse" in why
    assert "<previous_repair_attempt>" in seen["user"]
    assert "broke the suite" in seen["user"]

    seen.clear()
    autopilot._fix_iteration(tmp_path, "mock", "m", [_finding()])
    assert "<previous_repair_attempt>" not in seen["user"], (
        "a first attempt must not carry an empty history block"
    )


def test_identical_failures_are_not_narrated_twice(monkeypatch, tmp_path):
    """The stubbed shape three suites already use — every attempt failing
    the same way — must read as one reason, not an echo."""
    calls = _spy(monkeypatch, [
        (False, None, "the repair wrote no files"),
        (False, None, "the repair wrote no files"),
    ])

    _v, detail, _a, _bv, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t8",
    )

    assert len(calls) == autopilot.MAX_REPAIR_ATTEMPTS
    assert detail.count("the repair wrote no files") == 1
