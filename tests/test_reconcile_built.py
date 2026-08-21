"""Restoring a lost `built` flag, without resurrecting a superseded spec.

The flag matters because `built_task_ids` is what a resumed run reads to
decide what it may skip — a lost flag means the next `avs create` rebuilds
and re-bills work that is already committed.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.plan import built_task_ids, reconcile_built_flags
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _spec(root, slug, task_id, title, *, built):
    d = root / "specs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.yaml").write_text(yaml.safe_dump({
        "slug": slug, "title": title, "request": f"{title} (task:{task_id})",
        "status": "approved", "built": built, "criteria": [], "test_skeletons": [],
    }), encoding="utf-8")
    return d


def _commit(root, subject):
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", subject],
        cwd=root, check=True, capture_output=True,
    )


def _outcomes(root, rows):
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(
        yaml.safe_dump(rows), encoding="utf-8"
    )


def test_a_lost_flag_is_found_and_restored(tmp_path):
    root = init_workspace(tmp_path / "lost", "lost", "web")
    _spec(root, "task-store", "t1", "Task store", built=False)
    _outcomes(root, [{"task_id": "t1", "title": "Task store", "status": "built"}])
    _commit(root, "feat(task-store): Task store")
    assert built_task_ids(root) == set(), "fixture is not damaged"

    report = reconcile_built_flags(root)          # report only
    assert [e["task_id"] for e in report["lost"]] == ["t1"]
    assert report["repaired"] == [] and built_task_ids(root) == set()

    report = reconcile_built_flags(root, apply=True)
    assert [e["task_id"] for e in report["repaired"]] == ["t1"]
    assert built_task_ids(root) == {"t1"}, "the resumed run would still re-pay"


def test_a_superseded_spec_is_left_alone(tmp_path):
    """Planning is not deterministic: a re-run leaves the same task a second
    spec under a different slug, and the older one keeps built: false. Four
    such pairs sit in one real workspace. Repairing them would resurrect a
    spec a later plan replaced — and the task is not at risk, because its
    other spec already carries the flag."""
    root = init_workspace(tmp_path / "dup", "dup", "web")
    _spec(root, "reviews-text-star", "t5", "Reviews (text + star)", built=False)
    _spec(root, "reviews-with-text-and-star", "t5",
          "Reviews with Text and Star", built=True)
    _outcomes(root, [{"task_id": "t5", "title": "Reviews", "status": "built"}])
    _commit(root, "feat(reviews-with-text-and-star): Reviews with Text and Star")

    report = reconcile_built_flags(root, apply=True)

    assert report["repaired"] == [] and report["lost"] == []
    assert report["status"] == "clean"
    assert {e["slug"] for e in report["superseded"]} == {"reviews-text-star"}
    stale = yaml.safe_load(
        (root / "specs" / "reviews-text-star" / "spec.yaml").read_text()
    )
    assert stale["built"] is False, "a superseded spec was resurrected"


def test_a_task_with_no_commit_is_named_never_repaired(tmp_path):
    """outcomes says built and git does not: do not guess which is right."""
    root = init_workspace(tmp_path / "nocommit", "nocommit", "web")
    _spec(root, "ghost", "t9", "Ghost module", built=False)
    _outcomes(root, [{"task_id": "t9", "title": "Ghost", "status": "built"}])
    _commit(root, "chore: unrelated")

    report = reconcile_built_flags(root, apply=True)

    assert [e["task_id"] for e in report["unsupported"]] == ["t9"]
    assert report["repaired"] == [] and report["status"] == "needs_a_human"
    assert built_task_ids(root) == set()


def test_a_genuinely_unbuilt_task_is_not_touched(tmp_path):
    root = init_workspace(tmp_path / "failed", "failed", "web")
    _spec(root, "broken", "t2", "Broken module", built=False)
    _outcomes(root, [{"task_id": "t2", "title": "Broken", "status": "build_failed"}])
    _commit(root, "chore: nothing built")

    report = reconcile_built_flags(root, apply=True)

    assert report["status"] == "clean" and report["repaired"] == []


def test_a_workspace_with_no_product_says_so(tmp_path):
    root = init_workspace(tmp_path / "fresh", "fresh", "web")
    assert reconcile_built_flags(root)["status"] == "not_a_built_workspace"


def test_the_cli_reports_by_default_and_exits_three_on_a_finding(tmp_path):
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    root = init_workspace(tmp_path / "cli", "cli", "web")
    _spec(root, "task-store", "t1", "Task store", built=False)
    _outcomes(root, [{"task_id": "t1", "title": "Task store", "status": "built"}])
    _commit(root, "feat(task-store): Task store")

    result = CliRunner().invoke(app, ["reconcile", "--repo-dir", str(root)])
    assert result.exit_code == 3, result.output
    assert "t1" in result.output and "charge you again" in result.output
    assert built_task_ids(root) == set(), "a report-only run changed the workspace"

    result = CliRunner().invoke(
        app, ["reconcile", "--repo-dir", str(root), "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert built_task_ids(root) == {"t1"}
