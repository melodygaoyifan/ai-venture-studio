"""A parallel build is not a lesser build.

`--parallel` schedules one task per lane per wave, builds each in its own
worktree branch, and merges serially. Everything up to the merge was right;
everything after it was missing. `_build_wave_parallel` recorded

    TaskOutcome(status="built", detail=f"parallel lane {task.lane}")

and returned — no `review_and_repair`, so `review_verdict` was None, and no
`iterations` / `files_written` / `test_summary`, so the diagnosis fields were
empty too. A founder who passed `--parallel` got modules no reviewer had ever
looked at, sitting in the same report beside sequentially-built ones that
carried a real verdict, with nothing in the row saying which was which.

This is the hole `retry-task` shipped with, which is why `review_and_repair`
was extracted in the first place ("A retry is not a lesser build"). It
survived here because the wave loop is hand-written rather than routed
through `_attempt_task` — the same way the retry paths were hand-copied
variants before they were merged.

Second failure, found while closing the first: `finalize_build_bookkeeping`
ran AFTER the merge commit and was left uncommitted. `build.py` had already
learned this one commit earlier — "BEFORE the commit, not after", because
`git checkout -- .` in a rolled-back repair discards exactly those files, and
a resumed run then re-pays for modules it already built. Adding a review to
this path is what made an uncommitted-bookkeeping window reachable, so the
merge now carries the bookkeeping inside it.
"""
from __future__ import annotations

import subprocess
import types

import pytest

from ai_venture_studio.upstream import autopilot
from ai_venture_studio.executables import resolve


def _git(root, *args):
    return subprocess.run(
        [resolve("git"), *args], cwd=root, capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("seed\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "seed")
    return tmp_path


def _task(task_id="t1", lane="core"):
    return types.SimpleNamespace(
        id=task_id, title=f"Task {task_id}", description=f"build {task_id}",
        lane=lane, estimate_hours=1.0, depends_on=[],
    )


@pytest.fixture
def wired(repo, monkeypatch):
    """The wave loop with the model calls stubbed and git left real.

    Git stays real because the two things under test — that the review runs
    and that the bookkeeping is inside the merge commit — are both properties
    of what git ends up holding.
    """
    from ai_venture_studio.upstream import build as build_mod

    calls = {"reviewed": [], "finalized": []}

    def _fake_spec(root, description, **kw):
        slug = description.split("task:")[1].rstrip(")")
        (root / "specs" / slug).mkdir(parents=True, exist_ok=True)
        (root / "specs" / slug / "spec.yaml").write_text(f"slug: {slug}\n")
        return types.SimpleNamespace(slug=slug, status="proposed", block_reasons=[])

    def _fake_build(root, slug, **kw):
        # What run_build(in_branch=True) leaves behind: branch build/<slug>
        # carrying the work. A real worktree, outside the repo, because these
        # run concurrently in a ThreadPoolExecutor — a stub that checked the
        # branch out in place would race itself and fail as a merge conflict
        # that the product never had.
        tree = root.parent / f"wt-{slug}"
        _git(root, "worktree", "add", "-q", "-b", f"build/{slug}", str(tree))
        (tree / f"{slug}.py").write_text("def run():\n    return 1\n")
        _git(tree, "add", "-A")
        _git(tree, "commit", "-qm", f"feat({slug})")
        _git(root, "worktree", "remove", "--force", str(tree))
        # Every diagnosis field a real BuildResult carries, including the two
        # ADR-060 added — a stand-in that is thinner than the thing it stands
        # in for tests a narrower path than the one that ships.
        return types.SimpleNamespace(
            slug=slug, status="built", detail="", iterations=2,
            files_written=[f"{slug}.py"], test_summary="3 passed",
            modified_existing=[], wireup_issues=[],
        )

    def _fake_finalize(root, slug, files):
        calls["finalized"].append(slug)
        (root / "CHANGELOG.md").write_text(f"- {slug}\n")

    def _fake_review(root, *, provider, model, label, task_id="", detail=""):
        calls["reviewed"].append(label)
        return "APPROVE", f"{detail} reviewed", [f"fix iteration ({label}): none"], {}

    monkeypatch.setattr(autopilot, "run_spec_stage", _fake_spec)
    monkeypatch.setattr(autopilot, "approve_spec", lambda root, slug: None)
    monkeypatch.setattr(autopilot, "run_build", _fake_build)
    monkeypatch.setattr(build_mod, "finalize_build_bookkeeping", _fake_finalize)
    monkeypatch.setattr(autopilot, "review_and_repair", _fake_review)
    return calls


def test_a_merged_parallel_task_is_reviewed(repo, wired):
    """The gap itself: `review_verdict` was None on every parallel task."""
    approvals: list[str] = []
    outcomes = autopilot._build_wave_parallel(
        repo, [_task("t1"), _task("t2", lane="ui")],
        provider="mock", model="m", auto_approvals=approvals,
    )
    assert [o.status for o in outcomes] == ["built", "built"]
    assert wired["reviewed"] == ["t1", "t2"], "each merged task must be reviewed"
    for outcome in outcomes:
        assert outcome.review_verdict == "APPROVE", (
            "a parallel task reached the report with no verdict — "
            "unreviewed code wearing the same row as reviewed code"
        )
    assert any("fix iteration" in line for line in approvals), (
        "what the review did on the founder's behalf must reach the report"
    )


def test_the_diagnosis_fields_survive_the_merge(repo, wired):
    """`iterations`, `files_written` and `test_summary` were dropped here and
    carried everywhere else, so `--parallel` looked thinner in the record as
    well as being thinner."""
    outcomes = autopilot._build_wave_parallel(
        repo, [_task("t1")], provider="mock", model="m", auto_approvals=[],
    )
    (out,) = outcomes
    assert out.iterations == 2
    assert out.files_written == ["t1.py"]
    assert out.test_summary == "3 passed"
    assert "parallel lane core" in out.detail, "the lane must still be named"


def test_the_bookkeeping_is_inside_the_merge_commit(repo, wired):
    """Uncommitted bookkeeping under a review is bookkeeping waiting to be
    deleted: the repair rollback path runs `git checkout -- .`."""
    autopilot._build_wave_parallel(
        repo, [_task("t1")], provider="mock", model="m", auto_approvals=[],
    )
    assert wired["finalized"] == ["t1"]
    assert _git(repo, "status", "--porcelain").stdout == "", (
        "the merge left bookkeeping in the working tree, where a rolled-back "
        "repair discards it (build.py: 'BEFORE the commit, not after')"
    )
    tracked = _git(repo, "show", "--stat", "--format=", "HEAD").stdout
    assert "CHANGELOG.md" in tracked
    # Still a real merge commit, so `HEAD~1..HEAD` — what `_review_head`
    # reviews — is the whole merged branch and not just the bookkeeping.
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert len(parents) == 3, "the merge must keep both parents"


def test_a_conflicted_merge_is_still_not_reviewed(repo, wired, monkeypatch):
    """A task that never landed has nothing to review, and must not be
    recorded as though it did."""
    def _conflicting_build(root, slug, **kw):
        (root / "README.md").write_text("main side\n")
        _git(root, "commit", "-qam", "main moves")
        _git(root, "checkout", "-q", "-b", f"build/{slug}", "HEAD~1")
        (root / "README.md").write_text("branch side\n")
        _git(root, "commit", "-qam", f"feat({slug})")
        _git(root, "checkout", "-q", "main")
        return types.SimpleNamespace(
            slug=slug, status="built", detail="", iterations=1,
            files_written=[], test_summary="",
        )

    monkeypatch.setattr(autopilot, "run_build", _conflicting_build)
    outcomes = autopilot._build_wave_parallel(
        repo, [_task("t1")], provider="mock", model="m", auto_approvals=[],
    )
    assert [o.status for o in outcomes] == ["merge_conflict"]
    assert not wired["reviewed"]
    assert _git(repo, "status", "--porcelain").stdout == "", "the abort must be clean"


def test_a_failed_build_is_not_reviewed(repo, wired, monkeypatch):
    monkeypatch.setattr(
        autopilot, "run_build",
        lambda root, slug, **kw: types.SimpleNamespace(
            slug=slug, status="build_failed", detail="suite still red",
            iterations=3, files_written=[], test_summary="2 failed",
        ),
    )
    outcomes = autopilot._build_wave_parallel(
        repo, [_task("t1")], provider="mock", model="m", auto_approvals=[],
    )
    assert [o.status for o in outcomes] == ["build_failed"]
    assert outcomes[0].detail == "suite still red"
    assert not wired["reviewed"]


def test_both_build_paths_route_through_the_same_review(repo):
    """The structural half. `_attempt_task` and `_build_wave_parallel` are two
    hand-written loops; what must not drift again is that both call the one
    reviewed-build helper rather than re-implementing the tail of one."""
    import inspect

    for func in (autopilot._attempt_task, autopilot._build_wave_parallel):
        source = inspect.getsource(func)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "review_and_repair(" in code, (
            f"{func.__name__} builds modules without routing them through "
            "the shared review"
        )
