"""A fix that makes the product worse must not stand.

Real sequence, run 3:

  review 1  high/certain  "cart.js onAdd handler is never wired to any UI
                           control" — suggested fix: add the control
  fix       deletes the handler instead of adding the control
  review 2  critical/certain, VERIFIED, score 100
            "Add-to-cart handler and persistence removed, breaking core
             feature" ... "breaking existing page test"
  result    committed anyway; the second review was written into the report
            and acted on by nobody; the next four tasks could not build.

The re-review already ran. It just ran in the caller, after the commit,
with no authority.
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

from ai_venture_studio.state import Confidence, LeaderResult, Severity, Verdict, VoterFinding
from ai_venture_studio.upstream import autopilot


def _finding(sev):
    return VoterFinding(
        voter="correctness", title="t", severity=sev, confidence=Confidence.CERTAIN,
        file_path="miniprogram/pages/cart/cart.js", line_start=1, line_end=1,
        evidence="-const { addToCart } = require('../../utils/cart')",
        explanation="the handler was removed",
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "cart.js").write_text("onAdd(){}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "feat: cart"], cwd=root, check=True)
    return root


def _patch(monkeypatch, review_after, written=("cart.js",)):
    """A fix that reports `written`, a clean suite, and a given re-review.

    _write_files and _pytest_in_subprocess are imported INSIDE
    _fix_iteration, so they must be patched where they live.
    """
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream import build as build_mod

    monkeypatch.setattr(
        autopilot, "get_provider",
        lambda name: type("P", (), {
            "complete": staticmethod(lambda **k: "files: []")})(),
    )
    monkeypatch.setattr(
        build_mod, "_write_files", lambda root, f, **kw: (list(written), [])
    )
    monkeypatch.setattr(
        testing_mod, "_pytest_in_subprocess",
        lambda root: testing_mod.TestReport(status="no_tests", summary="none"),
    )
    monkeypatch.setattr(testing_mod, "run_js_tests", lambda root: None)
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review_after)


def test_a_fix_that_still_has_criticals_is_rolled_back(repo, monkeypatch):
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
    (repo / "cart.js").write_text("gutted\n", encoding="utf-8")
    _patch(monkeypatch, LeaderResult(
        verdict=Verdict.REQUEST_CHANGES, summary="broke core feature",
        findings=[_finding(Severity.CRITICAL)]))

    landed, after, _why = autopilot._fix_iteration(repo, "mock", "m", [_finding(Severity.HIGH)])

    assert landed is False
    assert after is not None and after.verdict == Verdict.REQUEST_CHANGES
    head_now = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
    assert head_now == head_before, "the bad fix stayed in history"
    assert (repo / "cart.js").read_text() == "onAdd(){}\n", "the file was not restored"


def test_a_clean_fix_lands(repo, monkeypatch):
    (repo / "cart.js").write_text("onAdd(){}\n// repaired\n", encoding="utf-8")
    _patch(monkeypatch, LeaderResult(verdict=Verdict.APPROVE, summary="clean"))

    landed, after, _why = autopilot._fix_iteration(repo, "mock", "m", [_finding(Severity.HIGH)])

    assert landed is True
    assert after.verdict == Verdict.APPROVE
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "address serious review findings" in log


def test_a_medium_finding_does_not_veto_the_fix(repo, monkeypatch):
    """Only critical/high block — the same bar the caller used to pick
    findings to feed back."""
    (repo / "cart.js").write_text("onAdd(){}\n// repaired\n", encoding="utf-8")
    _patch(monkeypatch, LeaderResult(
        verdict=Verdict.APPROVE_WITH_NOTES, summary="notes",
        findings=[_finding(Severity.MEDIUM)]))

    landed, _after, _why = autopilot._fix_iteration(repo, "mock", "m", [_finding(Severity.HIGH)])
    assert landed is True


def test_the_review_is_no_longer_run_twice():
    """It moved rather than multiplied — a fix costs the same as before."""
    for caller in (autopilot.run_autopilot, autopilot.run_feature):
        source = inspect.getsource(caller)
        assert "_review_head(root, provider)" not in source.split("_fix_iteration")[-1], (
            "the caller re-reviews again after the fix already did"
        )
