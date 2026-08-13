import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ai_venture_studio.orchestrator import run_review
from ai_venture_studio.testing import run_test_gate

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def _git_repo(tmp_path: Path, test_body: str) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(test_body)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


PASSING = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FAILING = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 4\n"


def test_gate_passes_on_green_suite(tmp_path):
    repo = _git_repo(tmp_path, PASSING)
    report = run_test_gate(str(repo), "")
    assert report.status == "passed"
    assert not report.gate_blocks


def test_gate_fails_on_red_suite(tmp_path):
    repo = _git_repo(tmp_path, FAILING)
    report = run_test_gate(str(repo), "")
    assert report.status == "failed"
    assert report.gate_blocks


def test_gate_applies_diff_in_worktree_not_checkout(tmp_path):
    repo = _git_repo(tmp_path, PASSING)
    # Diff breaks add(); suite must fail in the worktree while the user's
    # checkout stays untouched.
    breaking_diff = """\
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""
    report = run_test_gate(str(repo), breaking_diff)
    assert report.status == "failed"
    assert "return a + b" in (repo / "calc.py").read_text()


def test_gate_skipped_outside_git(tmp_path):
    assert run_test_gate(str(tmp_path), "").status == "skipped"


def test_gate_error_blocks_approve(tmp_path):
    """Found by self-review of PR #3: an unrunnable suite must not let
    APPROVE survive (charter rule 9)."""
    repo = _git_repo(tmp_path, PASSING)
    non_applying = """\
diff --git a/nonexistent.py b/nonexistent.py
--- a/nonexistent.py
+++ b/nonexistent.py
@@ -1,1 +1,1 @@
-old line that is not there
+new line
"""
    report = run_test_gate(str(repo), non_applying)
    assert report.status == "error"
    assert report.gate_blocks


def test_e2e_gate2_downgrades_approve(tmp_path, skills_dir):
    """Mock voters find nothing in a benign diff -> APPROVE, but the failing
    suite must force REQUEST_CHANGES."""
    repo = _git_repo(tmp_path, FAILING)
    benign_diff = """\
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
 def add(a, b):
     return a + b
+# benign trailing comment
"""
    result, state = run_review(
        "fixture://gate2",
        repo_dir=str(repo),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=benign_diff,
    )
    assert result.verdict.value == "REQUEST_CHANGES"
    assert "Gate 2" in result.summary
    assert state["test_report"]["status"] == "failed"


def test_e2e_voter_logs_appended(tmp_path, planted_diff_text, skills_dir):
    _, state = run_review(
        "fixture://logs",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )
    log = tmp_path / ".mas" / "voters" / "correctness" / "log.yaml"
    entries = yaml.safe_load(log.read_text())
    assert entries[0]["review_id"] == state["review_id"]
    assert entries[0]["status"] == "OK"


def test_a_hanging_suite_blocks_the_gate_instead_of_killing_the_run(monkeypatch, tmp_path):
    """`run_test_gate` guarded its own path, but build/autopilot/correction/
    fixpr call the runners directly. In bench run 12 one product whose tests
    never returned raised TimeoutExpired out of everything above it and took
    the whole case down — which then scored zero against a kill criterion.
    """
    import subprocess

    from ai_venture_studio import testing

    def _hang(cmd, cwd, timeout=testing._TEST_TIMEOUT_S):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(testing, "_run", _hang)
    report = testing._pytest_in_subprocess(tmp_path)

    assert report.status == "error"
    assert "exceeded" in report.summary
    # An unprovable suite must not pass — 'error' already blocks the gate.
    assert report.gate_blocks is True


# --- a hung suite must name itself, and must not outlive its own timeout ----
#
# Bench run 12's case 04 died on `pytest -q` exceeding 300s and the hang was
# never explained. Two reasons, both here rather than in the product: the
# timeout report carried the command line and nothing else, though
# `TimeoutExpired` was holding the output the whole time; and nothing asked
# pytest where it was stuck. A timeout that reports only that time passed
# cannot be diagnosed after the fact — which is how "unexplained" happened.


def test_a_hung_suite_reports_which_test_hung(tmp_path, monkeypatch):
    """The report must contain the hung test's name, not just an elapsed time."""
    from ai_venture_studio import testing as t

    proj = tmp_path / "hang"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_never_returns():\n    time.sleep(300)\n"
    )
    # Both timers scaled down: dump at 2s, kill at 6s.
    monkeypatch.setattr(t, "_HANG_DUMP_S", 2)
    monkeypatch.setattr(t, "_TEST_TIMEOUT_S", 6)
    report = t._run_and_classify(t.pytest_cmd(proj), proj)

    assert report.status == "error"
    assert report.gate_blocks, "an unprovable suite must not pass the gate"
    assert "test_never_returns" in report.detail, (
        "the timeout report does not say which test hung — this is exactly "
        f"what left run 12 unexplained. detail was: {report.detail!r}"
    )


def test_a_killed_suite_does_not_leave_a_server_running(tmp_path, monkeypatch):
    """Killing only the direct child orphans whatever the tests started.

    A generated product's tests routinely boot a server. `subprocess.run`'s
    kill signals pytest alone, so the server survives the timeout holding its
    port against the next case and the stdout pipe it inherited.
    """
    from ai_venture_studio import testing as t

    proj = tmp_path / "spawner"
    (proj / "tests").mkdir(parents=True)
    marker = tmp_path / "child-was-here"
    (proj / "tests" / "test_spawns.py").write_text(
        "import subprocess, sys, time\n"
        "\n"
        "\n"
        "def test_starts_a_server_then_hangs():\n"
        f"    subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(300)\"])\n"
        f"    open({str(marker)!r}, 'w').write('spawned')\n"
        "    time.sleep(300)\n"
    )
    monkeypatch.setattr(t, "_TEST_TIMEOUT_S", 6)
    monkeypatch.setattr(t, "_HANG_DUMP_S", 2)

    with pytest.raises(subprocess.TimeoutExpired):
        t._run(t.pytest_cmd(proj), proj, timeout=6)

    assert marker.exists(), "the test never got far enough to spawn anything"
    # The whole group is gone, so nothing is left holding the pipes; if the
    # grandchild had survived, _run's second communicate() would have blocked
    # here instead of returning.
    survivors = subprocess.run(
        ["pgrep", "-g", "0", "-f", "time.sleep(300)"], capture_output=True, text=True
    )
    assert "time.sleep(300)" not in survivors.stdout


def test_the_timeout_report_says_so_when_the_suite_printed_nothing():
    """No output is a fact worth stating, not a blank field."""
    from ai_venture_studio import testing as t

    exc = subprocess.TimeoutExpired(["pytest"], 300, output="", stderr="")
    assert "printed nothing" in t._hang_detail(["pytest"], exc)


def test_timeout_output_is_readable_even_when_python_hands_back_bytes():
    """CPython leaves TimeoutExpired's output as bytes under text=True."""
    from ai_venture_studio import testing as t

    exc = subprocess.TimeoutExpired(["pytest"], 300, output=b"dots", stderr=b"boom")
    detail = t._hang_detail(["pytest"], exc)
    assert "boom" in detail and "dots" in detail


def test_the_hang_dump_fires_before_the_suite_is_killed():
    """A dump scheduled after the kill would never be written."""
    from ai_venture_studio import testing as t

    assert t._HANG_DUMP_S < t._TEST_TIMEOUT_S
    assert f"faulthandler_timeout={t._HANG_DUMP_S}" in " ".join(t.pytest_flags())


def test_the_faulthandler_dump_is_clipped_from_the_top_not_the_bottom():
    """The hung test's frame is the FIRST line of the dump.

    faulthandler prints most-recent-call-first, so a tail-only clip keeps a
    page of pluggy internals and drops the one line that names the test —
    caught by the test above failing against the first version of this.
    """
    from ai_venture_studio import testing as t

    dump = "line 5 in test_the_one_that_hung\n" + "  File pluggy.py\n" * 500
    detail = t._hang_detail(["pytest"], subprocess.TimeoutExpired(["pytest"], 300, stderr=dump))
    assert "test_the_one_that_hung" in detail
    assert "elided" in detail  # and it is still clipped, not dumped whole


@pytest.mark.parametrize("wedged", ["suite", "sync"])
def test_a_hang_inside_the_docker_sandbox_blocks_the_gate_too(monkeypatch, tmp_path, wedged):
    """The T3 path called `_run` bare, so a sandboxed hang raised through.

    Same defect as the subprocess path, one sandbox over: the container is
    removed by the `finally`, but the exception still took out every caller.
    """
    from ai_venture_studio import testing as t

    def _fake_run(cmd, cwd, timeout=None):
        parts = [str(part) for part in cmd]
        # `docker exec … sh -c` is the dependency sync; any other exec is the
        # suite. Matching on a word would also match the tmp path and the
        # `pip install pytest` inside the sync command.
        is_exec = parts[:2] == ["docker", "exec"]
        is_sync = is_exec and parts[3:4] == ["sh"]
        if is_sync if wedged == "sync" else (is_exec and not is_sync):
            raise subprocess.TimeoutExpired(cmd, timeout or 300, stderr="stuck")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(t, "_run", _fake_run)
    report = t._pytest_in_docker(tmp_path)

    assert report.status == "error"
    assert "killed" in report.summary
    assert report.gate_blocks is True
