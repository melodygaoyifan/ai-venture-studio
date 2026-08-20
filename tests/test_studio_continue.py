"""One click continues the build — never one click per module.

The interrupted page and the failed-modules card offered only per-module
retry buttons: N mechanical clicks for something the resume machinery does
whole (locked plan reused free, built modules skipped, the rest attempted
with their recorded failure as context, auto-retry after). Both surfaces
now lead with a single "Continue the build" button posting /build; the
per-module buttons stay for surgical retries.

Plus two /retry regressions of previously-fixed bugs, re-fixed and pinned:
the retry worker's output went to DEVNULL (the v0.60 "worker dies with no
forensics" bug, reintroduced on this path) and it did not inherit the
Studio's --provider (a mock Studio spawned a retry that wanted a real key).
"""

import shutil
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.upstream import init_workspace

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


@pytest.fixture
def studio(tmp_path):
    root = init_workspace(tmp_path / "prod", "prod", "web")
    spawned: list = []
    client = TestClient(
        create_studio_app(root, spawn=lambda r: spawned.append(r) or 4242,
                          provider="mock")
    )
    return client, root, spawned


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _interrupted_workspace(root) -> None:
    """A dead worker, progress recorded, no report — the interrupted state."""
    from ai_venture_studio.upstream import progress

    progress.step(root, "t1", "build", "done")
    (root / "specs" / "one").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "one" / "spec.yaml").write_text(
        yaml.safe_dump({"request": "one (task:t1)", "built": True}),
        encoding="utf-8",
    )
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "plan.yaml").write_text(yaml.safe_dump({
        "status": "locked", "brief_title": "b", "tasks": [
            {"id": "t1", "title": "one", "estimate_hours": 1},
            {"id": "t2", "title": "two", "estimate_hours": 1},
        ]}), encoding="utf-8")
    (root / ".mas").mkdir(exist_ok=True)
    (root / ".mas" / "build.pid").write_text(str(_dead_pid()), encoding="utf-8")


def test_the_interrupted_page_continues_in_one_click(studio):
    client, root, spawned = studio
    _interrupted_workspace(root)

    page = client.get("/").text
    assert "Continue the build" in page
    assert "action=/build" in page
    assert "Resume" in page, "per-module buttons stay for surgical retries"

    client.post("/build", follow_redirects=False)
    assert spawned == [root], "one click respawned the one worker"


def test_the_failed_modules_card_continues_in_one_click(studio):
    client, root, spawned = studio
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "BUILD-REPORT.md").write_text("# done", encoding="utf-8")
    (root / "product" / "outcomes.yaml").write_text(yaml.safe_dump([
        {"task_id": "t1", "title": "one", "status": "built"},
        {"task_id": "t2", "title": "two", "status": "build_failed"},
    ]), encoding="utf-8")

    page = client.get("/").text

    assert "Modules that did not build" in page
    assert "Continue the build" in page and "action=/build" in page
    assert "action=/retry" in page  # the surgical option survives


def test_retry_inherits_the_provider_and_leaves_forensics(studio, monkeypatch):
    """The two regressions, pinned: --provider travels, and output goes to
    .mas/build.log — never DEVNULL.

    The workspace has to hold the plan the task belongs to: since the
    path-segment guard (v0.68.1) a `task_id` that is not in the plan is
    answered out loud instead of spawning a worker doomed on arrival, so a
    planless workspace never reaches the spawn this test is about.
    """
    import ai_venture_studio.studio as studio_mod

    client, root, _ = studio
    _interrupted_workspace(root)
    (root / ".mas" / "build.pid").unlink()  # no live worker in the way
    captured: dict = {}

    class _Proc:
        pid = 12345

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["stdout"] = kwargs.get("stdout")
        return _Proc()

    monkeypatch.setattr(studio_mod.subprocess, "Popen", fake_popen)
    client.post("/retry", data={"task_id": "t2"}, follow_redirects=False)

    assert "--provider" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--provider") + 1] == "mock"
    assert captured["stdout"] is not subprocess.DEVNULL
    assert captured["stdout"] is not None, "output must land in build.log"
    captured["stdout"].close()
    assert (root / ".mas" / "build.log").exists()
    assert (root / ".mas" / "build.pid").read_text() == "12345"
