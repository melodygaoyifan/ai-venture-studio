"""Bench run 19, case 03 t4 (团购汇总端点): Gate 2 refused a committed change.

The gate re-applied the reviewed diff onto a HEAD worktree, but `git diff`
without `--binary` describes a binary file as a stub line that `git apply`
refuses outright ("cannot apply binary patch to 'data/app.db' without full
index line") — so a sqlite db committed alongside the code blocked the gate
on an apply that had nothing to test. For a committed range, the worktree at
the range's tip already IS the post-image; the gate now checks the tip out
and skips the apply. A caller-supplied diff has no committed tip to trust,
so it keeps the apply path.
"""

from __future__ import annotations

import subprocess

from ai_venture_studio import testing
from ai_venture_studio.executables import resolve
from ai_venture_studio.orchestrator.graph import test_gate_node as gate2_node
from ai_venture_studio.testing import range_tip, run_test_gate


def _repo_with_binary_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = resolve("git")

    def g(*args):
        subprocess.run(
            [git, *args], cwd=repo, check=True, capture_output=True, timeout=60
        )

    g("init", "-q")
    g("config", "user.email", "t@t.invalid")
    g("config", "user.name", "t")
    (repo / "app.py").write_text("def double(x):\n    return x * 2\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import double\n\n\ndef test_double():\n    assert double(2) == 4\n"
    )
    (repo / "data.db").write_bytes(b"SQLite format 3\x00\x01\x02")
    g("add", "-A")
    g("commit", "-qm", "one")
    (repo / "app.py").write_text("def double(x):\n    return 2 * x\n")
    (repo / "data.db").write_bytes(b"SQLite format 3\x00\x09\x08")
    g("add", "-A")
    g("commit", "-qm", "two")
    diff = subprocess.run(
        [git, "diff", "HEAD~1..HEAD"],
        cwd=repo, capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    head = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip()
    return repo, diff, head


def test_range_tip_resolves_a_committed_range_and_nothing_else(tmp_path):
    repo, _, head = _repo_with_binary_change(tmp_path)
    assert range_tip("HEAD~1..HEAD", repo) == head
    assert range_tip("HEAD~1...HEAD", repo) == head
    # A single revision's post-image is the working tree, not a commit.
    assert range_tip("HEAD~1", repo) is None
    assert range_tip("https://github.com/o/r/pull/1", repo) is None
    assert range_tip("nope..alsonope", repo) is None


def test_the_apply_path_still_refuses_a_binary_stub(tmp_path):
    """The control: the failure the checkout path exists for."""
    repo, diff, _ = _repo_with_binary_change(tmp_path)
    report = run_test_gate(str(repo), diff)
    assert report.status == "error"
    assert "did not apply cleanly" in report.summary


def test_a_committed_range_is_gated_at_its_own_tip(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "docker_available", lambda: False)
    repo, diff, head = _repo_with_binary_change(tmp_path)
    report = run_test_gate(str(repo), diff, checkout=head)
    assert report.status == "passed", report.detail


def test_the_gate_node_hands_the_range_tip_to_the_gate(tmp_path, monkeypatch):
    repo, diff, head = _repo_with_binary_change(tmp_path)
    captured: dict = {}

    def fake_gate(repo_dir, diff_raw, **kwargs):
        captured.update(kwargs)
        return testing.TestReport(status="passed", summary="ok")

    monkeypatch.setattr(testing, "run_test_gate", fake_gate)
    state = {
        "target": "HEAD~1..HEAD",
        "mode": "standard",
        "diff": {"raw": diff, "changed_files": ["app.py"]},
        "leader": {"verdict": "APPROVE"},
    }
    gate2_node(state, repo_dir=str(repo))
    assert captured["checkout"] == head


def test_a_caller_supplied_diff_keeps_the_apply_path(tmp_path, monkeypatch):
    repo, diff, _ = _repo_with_binary_change(tmp_path)
    captured: dict = {}

    def fake_gate(repo_dir, diff_raw, **kwargs):
        captured.update(kwargs)
        return testing.TestReport(status="passed", summary="ok")

    monkeypatch.setattr(testing, "run_test_gate", fake_gate)
    state = {
        "target": "HEAD~1..HEAD",
        "mode": "standard",
        "diff": {"raw": diff, "changed_files": ["app.py"]},
        "diff_supplied": True,
        "leader": {"verdict": "APPROVE"},
    }
    gate2_node(state, repo_dir=str(repo))
    assert captured["checkout"] is None
