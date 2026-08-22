"""`built: true` has to survive the recovery paths, or the run re-pays.

A real live run committed six modules and kept the flag on two. The flag and
the changelog fragment were written AFTER the task's commit, so they sat in
the working tree — where `git checkout -- .` (a rolled-back fix iteration)
and `_reset_workspace` (a failed build) discard uncommitted changes
wholesale. `built_task_ids` then under-reported: the founder's report headline
read "2 of 6" over a finished product, and a resumed run would have rebuilt
and re-paid for four modules that were already built and committed.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def test_bookkeeping_is_inside_the_tasks_own_commit():
    """Ordering, asserted on the source: finalize must precede `git add`."""
    import inspect

    from ai_venture_studio.upstream import build

    src = inspect.getsource(build._run_build_inner)
    finalize = src.index("finalize_build_bookkeeping(repo, slug, written)")
    staged = src.index('resolve("git"), "add", "-A"')
    commit = src.index('f"feat({slug})')
    assert finalize < staged < commit, (
        "the built flag is written outside the commit again — a rollback "
        "will take it with the working tree"
    )


def _git(root, *args):
    return subprocess.run(
        [resolve("git"), *args], cwd=root, capture_output=True, text=True, timeout=60
    )


def test_a_discarded_working_tree_cannot_lose_the_flag(tmp_path):
    """The mechanism itself: once bookkeeping rides in the commit, the two
    recovery paths that discard uncommitted work cannot reach it.

    Driven, not described. The first version of this test hand-wrote
    `built: true` into the spec and hand-committed it, then asserted that a
    working-tree discard could not lose it — which is git's behaviour, true of
    any committed file, and true whether or not this repo writes the flag
    before the commit or after it. It survived every mutation of the fix it is
    named for because the fix never ran inside it. `finalize_build_bookkeeping`
    is the subject now, and both recovery paths run for real.
    """
    from ai_venture_studio.upstream import init_workspace
    from ai_venture_studio.upstream.build import (
        _reset_workspace,
        finalize_build_bookkeeping,
    )
    from ai_venture_studio.upstream.plan import built_task_ids

    root = init_workspace(tmp_path / "durable", "durable", "web")
    spec_dir = root / "specs" / "one"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(
        "slug: one\ntitle: One\nrequest: 'one (task:t1)'\nstatus: approved\n"
        "profile: web\ndesign: ''\n"
        "built: false\ncriteria: []\ntest_skeletons: []\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    pre_existing = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    assert built_task_ids(root) == set(), "nothing is built before the build"

    # The order `_run_build_inner` uses, and the whole of the fix: bookkeeping
    # first, staging second, so the flag is INSIDE the task's own commit.
    finalize_build_bookkeeping(root, "one", ["app.py"])
    assert built_task_ids(root) == {"t1"}, "the flag was never written at all"
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "feat(one)")

    # What a rolled-back fix iteration does, verbatim.
    _git(root, "checkout", "--", ".")
    assert built_task_ids(root) == {"t1"}, (
        "a committed flag was lost to a working-tree discard"
    )
    # And what a failed in-place build does — the second path the fix is for.
    _reset_workspace(root, pre_existing)
    assert built_task_ids(root) == {"t1"}, (
        "a committed flag was lost to the failed-build reset"
    )
