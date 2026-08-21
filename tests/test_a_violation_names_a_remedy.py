"""Deterministic plan feedback must name a legal arrangement (ADR-058).

Run 18's `03-groupbuy-auto` built nothing. The planner produced
`lane collision: t1 (api) and t3 (orders) both expect 'app/models*.py'`, was
handed that exact sentence back as revision feedback, produced a materially
identical plan, was handed it again, and was blocked at Gate U2 after
`MAX_REVISIONS`. The sentence is true and precise and unactionable: it names
the arrangement that is forbidden and never one that is allowed.

The same product's `01-groupbuy-api` had already solved the identical
collision — by hoisting the shared model file into its own task both others
depend on. That remedy was available the whole time and no message mentioned
it. ADR-041 one stage over: a writer that is not told what would count as
fixed cannot fix it.
"""

from __future__ import annotations

from ai_venture_studio.upstream.plan import Task, lane_check


def _task(tid: str, lane: str, files: list[str]) -> Task:
    return Task(
        id=tid, title=f"task {tid}", description="d", lane=lane,
        files_expected=files, estimate_hours=1.0,
    )


COLLIDING = [
    _task("t1", "api", ["app/models*.py", "app/api.py"]),
    _task("t3", "orders", ["app/models*.py", "app/orders.py"]),
]


def test_a_collision_is_still_reported():
    issues = lane_check(COLLIDING)
    assert len(issues) == 1
    assert "lane collision: t1 (api) and t3 (orders)" in issues[0]
    assert "app/models*.py" in issues[0]


def test_the_collision_message_names_a_legal_arrangement():
    """The whole fix: a planner reading this can produce a different plan."""
    text = issues[0] if (issues := lane_check(COLLIDING)) else ""
    assert "HOIST" in text and "MERGE" in text and "SPLIT" in text
    # ...and each remedy is concrete about THESE tasks, not a generic lecture.
    assert "make t1 and t3 depend on it" in text
    assert "put t3 in lane 'api'" in text


def test_the_pair_is_reported_once_not_once_per_glob():
    """Three overlapping globs between two tasks is one problem with one fix."""
    issues = lane_check([
        _task("t1", "api", ["a/*.py", "b/*.py", "c/*.py"]),
        _task("t2", "web", ["a/*.py", "b/*.py", "c/*.py"]),
    ])
    assert len(issues) == 1
    for glob in ("a/*.py", "b/*.py", "c/*.py"):
        assert glob in issues[0], "the message still has to name every overlap"


def test_same_lane_overlap_is_not_a_collision():
    """Lanes serialize — and remedy (2) tells the planner to use that."""
    assert lane_check([
        _task("t1", "api", ["app/models.py"]),
        _task("t2", "api", ["app/models.py"]),
    ]) == []


def test_the_remedy_the_message_recommends_actually_clears_the_check():
    """Do not tell a planner to do something the checker still rejects."""
    # (1) HOIST
    assert lane_check([
        _task("t0", "models", ["app/models*.py"]),
        _task("t1", "api", ["app/api.py"]),
        _task("t3", "orders", ["app/orders.py"]),
    ]) == []
    # (2) MERGE
    assert lane_check([
        _task("t1", "api", ["app/models*.py", "app/api.py"]),
        _task("t3", "api", ["app/models*.py", "app/orders.py"]),
    ]) == []
    # (3) SPLIT
    assert lane_check([
        _task("t1", "api", ["app/api.py"]),
        _task("t3", "orders", ["app/orders.py"]),
    ]) == []


def test_three_separate_pairs_are_three_separate_issues():
    issues = lane_check([
        _task("t1", "a", ["shared.py"]),
        _task("t2", "b", ["shared.py"]),
        _task("t3", "c", ["shared.py"]),
    ])
    assert len(issues) == 3
