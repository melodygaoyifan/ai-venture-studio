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
    recovery paths that discard uncommitted work cannot reach it."""
    from ai_venture_studio.upstream import init_workspace
    from ai_venture_studio.upstream.plan import built_task_ids

    root = init_workspace(tmp_path / "durable", "durable", "web")
    spec_dir = root / "specs" / "one"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(
        "slug: one\nrequest: 'one (task:t1)'\nstatus: approved\n"
        "built: true\ncriteria: []\ntest_skeletons: []\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "feat(one)")
    assert built_task_ids(root) == {"t1"}

    # What a rolled-back fix iteration does, verbatim.
    _git(root, "checkout", "--", ".")
    assert built_task_ids(root) == {"t1"}, (
        "a committed flag was lost to a working-tree discard"
    )
